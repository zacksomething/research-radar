"""Offline tests for collection correctness and downstream data preservation."""
import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import Mock, patch
from research_radar import collection as c


CFG = {'clusters': {'World-Models': {'include': ['world model'], 'exclude': []}, 'VLA': {'include': ['VLA'], 'exclude': []}}, 'scoring': {}, 'sources': {'arxiv': {'categories': ['cs.AI'], 'base_url': 'https://example.invalid/arxiv'}, 'huggingface_daily': {'url': 'https://example.invalid/hf'}}}


def paper(**changes):
    raw = {'id': 'arxiv:2609.00001', 'title': 'World model VLA', 'abstract': 'Tested on 100 tasks with 20 trials.', 'authors': [{'name': 'A', 'affiliations': ['Lab']}], 'url': 'https://arxiv.org/abs/2609.00001v1', 'published': '2026-09-18T00:00:00Z', 'updated': '2026-09-18T00:00:00Z', 'sources': ['arxiv']}
    raw.update(changes)
    return c.normalize_paper(raw)


def feed(entries=1, total=None):
    content = ''.join('<entry><id>https://arxiv.org/abs/2609.%05dv1</id><title>A &amp; B world model</title><summary>Full &lt;abstract&gt; 20</summary><author><name>A &amp; B</name><arxiv:affiliation>Lab &amp; Co</arxiv:affiliation></author><published>2026-09-18T00:00:00Z</published><updated>2026-09-18T01:00:00Z</updated><link title="pdf" href="https://arxiv.org/pdf/2609.%05dv1" /></entry>' % (i, i) for i in range(entries))
    count = '' if total is None else '<opensearch:totalResults>%d</opensearch:totalResults>' % total
    return ('<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom" xmlns:opensearch="http://a9.com/-/spec/opensearch/1.1/">' + count + content + '</feed>').encode()


class CollectionUnitTests(unittest.TestCase):
    def test_atom_entities_and_per_author_affiliations(self):
        papers, total = c.parse_arxiv_xml(feed(1, 1))
        self.assertEqual(total, 1)
        self.assertEqual(papers[0]['title'], 'A & B world model')
        self.assertEqual(papers[0]['abstract'], 'Full <abstract> 20')
        self.assertEqual(papers[0]['authors'], [{'name': 'A & B', 'affiliations': ['Lab & Co']}])
        self.assertTrue(papers[0]['pdf_url'].endswith('v1'))

    def test_atom_api_error_is_not_a_paper(self):
        with self.assertRaisesRegex(ValueError, 'API error'):
            c.parse_arxiv_xml(b'<feed><entry><id>http://arxiv.org/api/errors#bad_query</id><summary>bad query</summary></entry></feed>')

    def test_version_and_legacy_id_normalization(self):
        self.assertEqual(c.arxiv_id('https://arxiv.org/abs/2609.00001v12'), '2609.00001')
        self.assertEqual(c.arxiv_id('https://arxiv.org/abs/cs/9901001v2'), 'cs/9901001')
        self.assertEqual(c.normalize_paper({'id': 'synthetic:example', 'title': 'Demo'})['id'], 'synthetic:example')

    def test_arxiv_ids_require_whole_identifiers_or_official_paper_urls(self):
        accepted = {
            '2609.00001v12': '2609.00001',
            'arxiv:2609.00001v2': '2609.00001',
            'hep-th/9901001v2': 'hep-th/9901001',
            'https://arxiv.org/pdf/hep-th/9901001v2.pdf': 'hep-th/9901001',
            'http://export.arxiv.org/abs/cs/9901001v2': 'cs/9901001',
        }
        for value, expected in accepted.items():
            with self.subTest(value=value):
                self.assertEqual(c.arxiv_id(value), expected)
        for value in ('external:2609.00001', 'notes 2609.00001', '2609.00001-supplement',
                      'https://example.org/reports/2609.00001',
                      'https://arxiv.org.evil.example/abs/2609.00001',
                      'https://arxiv.org/search?query=2609.00001',
                      'https://arxiv.org/abs/2609.00001/supplement'):
            with self.subTest(value=value):
                self.assertEqual(c.arxiv_id(value), '')

    def test_explicit_external_identity_cannot_be_merged_with_arxiv_number(self):
        for url in ('https://example.org/reports/2609.00001', 'https://arxiv.org/abs/2609.00001'):
            with self.subTest(url=url):
                external = paper(id='external:2609.00001', url=url, title='Different publication')
                self.assertEqual(external['id'], 'external:2609.00001')
                self.assertIsNone(external['arxiv_version'])
                self.assertEqual(len(c.prepare_papers([external, paper()], {}, CFG)), 2)
        anonymous = c.normalize_paper({'title': 'External report', 'url': 'https://example.org/2609.00001'})
        self.assertTrue(anonymous['id'].startswith('external:'))

    def test_id_only_new_version_replaces_legacy_state_without_version_field(self):
        old = paper()
        del old['arxiv_version']
        # Migrating a legacy v1 state must not create an artificial update.
        unchanged = c.prepare_papers([paper()], {old['id']: old}, CFG)[0]
        self.assertEqual(unchanged['change'], 'seen')
        newer = paper(id='arxiv:2609.00001v2', url='https://arxiv.org/abs/2609.00001',
                      abstract='Revised 5', updated='2026-09-19T00:00:00Z')
        self.assertEqual(newer['arxiv_version'], 2)
        actual = c.prepare_papers([newer], {old['id']: old}, CFG)[0]
        self.assertEqual(actual['abstract'], 'Revised 5')
        self.assertEqual(actual['arxiv_version'], 2)
        self.assertEqual(actual['change'], 'updated')
        # A later stale observation cannot roll the stored v2 back to v1.
        again = c.prepare_papers([old], {actual['id']: actual}, CFG)[0]
        self.assertEqual(again['arxiv_version'], 2)
        self.assertEqual(again['abstract'], 'Revised 5')

    def test_conflicting_arxiv_identity_or_versions_are_rejected(self):
        for change in ({'id': 'arxiv:2609.00002'}, {'id': 'arxiv:2609.00001v2'},
                       {'arxiv_version': 2}, {'arxiv_version': True}):
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    paper(**change)

    def test_multilabel_and_unmatched_records_are_preserved(self):
        records = c.prepare_papers([paper(), paper(id='synthetic:unknown', url='https://example.invalid/unknown', title='Unrelated', abstract='Study 20')], {}, CFG)
        self.assertEqual(len(records), 2)
        self.assertEqual(next(p for p in records if p['id'].startswith('arxiv:'))['clusters'], ['World-Models', 'VLA'])
        unmatched = next(p for p in records if p['id'] == 'synthetic:unknown')
        self.assertEqual(unmatched['clusters'], [])
        self.assertIsNone(unmatched['primary_cluster'])

    def test_short_tokens_do_not_match_substrings(self):
        self.assertFalse(c.keyword_match('ACT', 'action abstract'))
        self.assertFalse(c.keyword_match('MATH', 'mathematical'))
        self.assertTrue(c.keyword_match('world model', 'world-model'))

    def test_prior_fixed_branches_and_signals(self):
        ten = c.score_paper(paper(hf_upvotes=10), {})[0]
        fifty = c.score_paper(paper(hf_upvotes=50), {})[0]
        self.assertEqual(fifty - ten, 1.5)
        _, signals = c.score_paper(paper(abstract='Code will be released for 20 tasks.'), {})
        self.assertIn('code_release_promised (unverified)', signals)
        self.assertFalse(any('code_available_claim' in s for s in signals))
        _, signals = c.score_paper(paper(abstract='100 tasks with 20 trials'), {})
        self.assertFalse(any('no_numeric' in s for s in signals))
        _, signals = c.score_paper(paper(abstract='action abstract mathematical 20'), {})
        self.assertFalse(any('baseline_mentioned' in s or 'benchmark_mentioned' in s for s in signals))

    def test_merge_keeps_latest_version_and_popularity(self):
        old = paper()
        new = paper(url='https://arxiv.org/abs/2609.00001v2', abstract='Revised 5', updated='2026-09-19T00:00:00Z')
        hf = paper(url='https://arxiv.org/abs/2609.00001', sources=['hf'], hf_upvotes=100, hf_featured_dates=['2026-09-19'], authors=['A'])
        records = c.prepare_papers([new, old, hf], {}, CFG)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['abstract'], 'Revised 5')
        self.assertEqual(records[0]['hf_upvotes'], 100)
        self.assertEqual(records[0]['authors'][0]['affiliations'], ['Lab'])
        self.assertEqual(records[0]['sources'], ['arxiv', 'hf'])

    def test_same_version_partial_author_list_cannot_delete_known_authors(self):
        full = paper(authors=[{'name': 'A', 'affiliations': ['Old Lab']}, 'B', 'C'])
        partial = paper(authors=[{'name': 'A', 'affiliations': ['Additional Lab']}])
        for observations in ([full, partial], [partial, full]):
            merged = c.prepare_papers(observations, {}, CFG)[0]
            self.assertEqual([a['name'] for a in merged['authors']], ['A', 'B', 'C'])
            self.assertEqual(merged['authors'][0]['affiliations'], ['Additional Lab', 'Old Lab'])
        stored = c.prepare_papers([partial], {full['id']: full}, CFG)[0]
        self.assertEqual([a['name'] for a in stored['authors']], ['A', 'B', 'C'])

    def test_newer_version_can_remove_an_author(self):
        full = paper(authors=['A', 'B', 'C'])
        revised = paper(url='https://arxiv.org/abs/2609.00001v2', authors=['A'],
                        updated='2026-09-19T00:00:00Z')
        for observations in ([full, revised], [revised, full]):
            merged = c.prepare_papers(observations, {}, CFG)[0]
            self.assertEqual(merged['arxiv_version'], 2)
            self.assertEqual([a['name'] for a in merged['authors']], ['A'])
        stored = c.prepare_papers([revised], {full['id']: full}, CFG)[0]
        self.assertEqual([a['name'] for a in stored['authors']], ['A'])

    def test_dated_external_revision_can_correct_an_author_list(self):
        full = paper(id='external:report', url='https://example.org/report',
                     authors=['A', 'B'], updated='2026-10-01')
        corrected = paper(id='external:report', url='https://example.org/report',
                          authors=['A'], updated='2026-10-08')
        stored = c.prepare_papers([corrected], {full['id']: full}, CFG)[0]
        self.assertIsNone(stored['arxiv_version'])
        self.assertEqual([a['name'] for a in stored['authors']], ['A'])
        self.assertEqual(stored['change'], 'updated')

    def test_hf_featured_old_paper_is_not_dropped(self):
        payload = [{'paper': {'id': '2608.00001', 'title': 'An older paper', 'summary': 'Full abstract', 'authors': [{'name': 'A'}], 'publishedAt': '2026-08-01T00:00:00Z', 'upvotes': 75}}]
        client = Mock()
        client.get.return_value = (json.dumps(payload).encode(), 1)
        records, result = c.fetch_hf(CFG, dt.date(2026, 9, 18), dt.date(2026, 9, 18), client, 10)
        self.assertEqual(result['status'], 'success')
        self.assertEqual(records[0]['published'], '2026-08-01T00:00:00Z')
        self.assertEqual(records[0]['hf_featured_dates'], ['2026-09-18'])
        self.assertIn('example.invalid/hf', client.get.call_args[0][0])

    def test_bad_hf_record_does_not_discard_remaining_valid_records(self):
        def valid(identifier):
            return {'paper': {'id': identifier, 'title': 'Valid paper', 'authors': [{'name': 'A'}]}}
        for invalid in (None, {'paper': []}, {'paper': {'id': '2609.00003'}},
                        {'paper': {'id': '', 'title': 'No identifier'}}):
            with self.subTest(invalid=invalid):
                payload = [valid('2609.00001'), invalid, valid('2609.00002')]
                client = Mock()
                client.get.return_value = (json.dumps(payload).encode(), 1)
                records, result = c.fetch_hf(CFG, dt.date(2026, 9, 18), dt.date(2026, 9, 18), client, 10)
                self.assertEqual([p['id'] for p in records], ['arxiv:2609.00001', 'arxiv:2609.00002'])
                self.assertEqual(result['status'], 'partial')
                self.assertEqual(result['count'], 2)
                self.assertEqual(len(result['errors']), 1)
                self.assertIn('2026-09-18: record 2:', result['errors'][0])

    def test_arxiv_cap_is_explicit_and_uses_config_url(self):
        client = Mock()
        client.get.return_value = (feed(2, 5), 1)
        records, result = c.fetch_arxiv(CFG, dt.date(2026, 9, 18), dt.date(2026, 9, 18), client, 2)
        self.assertEqual(len(records), 2)
        self.assertTrue(result['truncated'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['available_count'], 5)
        self.assertIn('example.invalid/arxiv', client.get.call_args[0][0])

    def test_arxiv_exact_total_is_complete(self):
        client = Mock()
        client.get.return_value = (feed(2, 2), 1)
        _, result = c.fetch_arxiv(CFG, dt.date(2026, 9, 18), dt.date(2026, 9, 18), client, 2)
        self.assertFalse(result['truncated'])
        self.assertEqual(result['status'], 'success')

    def test_partial_pagination_keeps_already_fetched_records(self):
        client = Mock()
        client.get.side_effect = [(feed(200, 201), 1), c.FetchError('offline', 3)]
        records, result = c.fetch_arxiv(CFG, dt.date(2026, 9, 18), dt.date(2026, 9, 18), client, 400)
        self.assertEqual(len(records), 200)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['attempts'], 4)
        self.assertEqual(result['errors'], ['offline'])

    def test_bounded_network_retries(self):
        with patch.object(c.urllib.request, 'urlopen', side_effect=OSError('offline')) as request, patch.object(c.time, 'sleep'):
            with self.assertRaises(c.FetchError) as caught:
                c.HttpClient(retries=2, interval=0).get('https://example.invalid')
        self.assertEqual(request.call_count, 3)
        self.assertEqual(caught.exception.attempts, 3)

    def test_non_retryable_http_failure(self):
        error = urllib.error.HTTPError('https://example.invalid', 404, 'Not found', {}, None)
        with patch.object(c.urllib.request, 'urlopen', side_effect=error) as request:
            with self.assertRaises(c.FetchError):
                c.HttpClient(retries=3).get('https://example.invalid')
        self.assertEqual(request.call_count, 1)


class CollectionCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps(CFG))
        self.input = self.root / 'input.json'
        self.input.write_text(json.dumps([paper()]))
        self.args = ['--source', 'file', '--input', str(self.input), '--config', str(self.config), '--data-dir', str(self.root / 'data'), '--date', '2026-09-18']

    def tearDown(self):
        self.temp.cleanup()

    def run_main(self, args=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = c.main(self.args if args is None else args)
        return code, json.loads(out.getvalue())

    def test_full_payload_custom_report_and_persistent_dedup(self):
        record = paper(abstract='A' * 1200, authors=[{'name': 'Author %d' % i, 'affiliations': ['Lab %d' % i]} for i in range(7)])
        other = paper(id='external:other', url='https://example.invalid/other', title='Unclassified research', abstract='20 tasks')
        self.input.write_text(json.dumps({'schema_version': 1, 'papers': [record, other]}))
        args = self.args + ['--out', str(self.root / 'separate' / 'daily.md'), '--cluster', 'VLA', '--top', '1']
        code, summary = self.run_main(args)
        self.assertEqual(code, 0)
        snapshot = json.loads(Path(summary['paths']['papers']).read_text())
        self.assertEqual(len(snapshot['papers']), 2)
        long = next(p for p in snapshot['papers'] if len(p['abstract']) > 1000)
        self.assertEqual(len(long['authors']), 7)
        report = Path(summary['paths']['report']).read_text()
        self.assertIn('A' * 1200, report)
        self.assertIn('Author 6', report)
        self.assertIn('Lab 6', report)
        self.assertFalse((self.root / 'Papers').exists())
        code, second = self.run_main(args)
        second_papers = json.loads(Path(second['paths']['papers']).read_text())['papers']
        self.assertEqual({p['change'] for p in second_papers}, {'seen'})
        self.assertNotEqual(summary['paths']['papers'], second['paths']['papers'])

    def test_changed_record_is_updated(self):
        self.run_main()
        updated = paper(abstract='Changed 2', url='https://arxiv.org/abs/2609.00001v2', updated='2026-09-19T00:00:00Z')
        self.input.write_text(json.dumps([updated]))
        code, summary = self.run_main()
        self.assertEqual(code, 0)
        record = json.loads(Path(summary['paths']['papers']).read_text())['papers'][0]
        self.assertEqual(record['change'], 'updated')
        self.assertEqual(record['abstract'], 'Changed 2')

    def test_file_cap_and_invalid_records_are_partial(self):
        self.input.write_text(json.dumps([paper(), {'authors': []}, paper(id='other', url='https://example.invalid')]))
        code, summary = self.run_main(self.args + ['--max-results', '2'])
        self.assertEqual(code, 2)
        manifest = json.loads(Path(summary['paths']['run']).read_text())
        self.assertEqual(manifest['status'], 'partial')
        self.assertTrue(manifest['sources'][0]['truncated'])
        self.assertIn('Record 2', manifest['sources'][0]['errors'][0])

    def test_import_retains_upstream_failure_and_truncation(self):
        cases = [
            ('partial', [{'name': 'arxiv', 'status': 'partial', 'truncated': True, 'errors': ['pagination interrupted']}]),
            ('failed', [{'name': 'hf', 'status': 'failed', 'truncated': False, 'errors': ['request failed']}]),
            ('success', [{'name': 'arxiv', 'status': 'success', 'truncated': True, 'errors': []}]),
        ]
        for status, sources in cases:
            with self.subTest(upstream_status=status):
                upstream = {'id': 'original-' + status, 'status': status, 'window': {'start': '2026-09-18', 'end': '2026-09-18'}, 'sources': sources}
                self.input.write_text(json.dumps({'schema_version': 1, 'run': upstream, 'papers': [paper()]}))
                code, summary = self.run_main()
                self.assertEqual(code, 2)
                self.assertEqual(summary['status'], 'partial')
                snapshot = json.loads(Path(summary['paths']['papers']).read_text())
                manifest = json.loads(Path(summary['paths']['run']).read_text())
                self.assertEqual(snapshot['run']['input_run'], upstream)
                self.assertEqual(manifest['input_run']['sources'], sources)
                self.assertEqual(manifest['sources'][0]['name'], 'file')
                self.assertEqual(manifest['sources'][0]['status'], 'partial')
                self.assertEqual(len(snapshot['papers']), 1)
                if sources[0]['errors']:
                    self.assertIn(sources[0]['errors'][0], Path(summary['paths']['report']).read_text())

    def test_complete_bundle_import_stays_success_and_retains_provenance(self):
        upstream = {'id': 'original-complete', 'status': 'success', 'sources': [{'name': 'arxiv', 'status': 'success', 'truncated': False, 'errors': []}]}
        self.input.write_text(json.dumps({'schema_version': 1, 'run': upstream, 'papers': [paper()]}))
        code, summary = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(summary['status'], 'success')
        manifest = json.loads(Path(summary['paths']['run']).read_text())
        self.assertEqual(manifest['input_run'], upstream)

    def test_reimported_partial_bundle_cannot_lose_coverage_warning(self):
        upstream = {'id': 'original-partial', 'status': 'partial', 'sources': [{'name': 'arxiv', 'status': 'partial', 'truncated': True, 'errors': ['pagination interrupted']}]}
        self.input.write_text(json.dumps({'schema_version': 1, 'run': upstream, 'papers': [paper()]}))
        _, first = self.run_main()
        self.input.write_text(Path(first['paths']['papers']).read_text())
        code, second = self.run_main()
        self.assertEqual(code, 2)
        manifest = json.loads(Path(second['paths']['run']).read_text())
        self.assertEqual(manifest['input_run']['input_run'], upstream)

    def test_both_sources_fail_with_failure_manifest(self):
        args = ['--source', 'both', '--config', str(self.config), '--data-dir', str(self.root / 'data'), '--date', '2026-09-18']
        with patch.object(c.HttpClient, 'get', side_effect=c.FetchError('offline', 3)):
            code, summary = self.run_main(args)
        self.assertEqual(code, 1)
        self.assertEqual(summary['status'], 'failed')
        manifest = json.loads(Path(summary['paths']['run']).read_text())
        self.assertEqual([s['status'] for s in manifest['sources']], ['failed', 'failed'])
        self.assertIn('offline', Path(summary['paths']['report']).read_text())
        self.assertFalse(Path(summary['paths']['state']).exists())

    def test_one_source_failure_returns_partial(self):
        args = ['--source', 'both', '--config', str(self.config), '--data-dir', str(self.root / 'data'), '--date', '2026-09-18']
        with patch.object(c.HttpClient, 'get', side_effect=[(feed(1, 1), 1), c.FetchError('offline', 1)]):
            code, summary = self.run_main(args)
        self.assertEqual(code, 2)
        self.assertEqual(summary['paper_count'], 1)
        self.assertEqual(summary['status'], 'partial')

    def test_invalid_days_and_missing_input_return_two(self):
        for args in [self.args + ['--days', '0'], self.args + ['--top', '-1'], ['--source', 'file']]:
            code, summary = self.run_main(args)
            self.assertEqual(code, 2)
            self.assertEqual(summary['status'], 'invalid_args')

    def test_failed_atomic_write_preserves_existing_data(self):
        target = self.root / 'target.json'
        target.write_text('old')
        with patch.object(c.os, 'replace', side_effect=OSError('injected')):
            with self.assertRaises(OSError):
                c.atomic_text(target, 'new')
        self.assertEqual(target.read_text(), 'old')
        self.assertEqual(list(self.root.glob('.target.json.*')), [])

    def test_corrupt_state_is_not_overwritten(self):
        path = self.root / 'data' / 'state' / 'papers.json'
        path.parent.mkdir(parents=True)
        path.write_text('{broken')
        code, summary = self.run_main()
        self.assertEqual(code, 1)
        self.assertEqual(summary['status'], 'failed')
        self.assertEqual(path.read_text(), '{broken')

    def test_default_date_is_yesterday_in_utc(self):
        expected = dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
        self.assertEqual(c.build_parser().parse_args([]).date, expected)



if __name__ == '__main__':
    unittest.main()
