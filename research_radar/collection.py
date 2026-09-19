"""Complete paper collection. Markdown is a view; papers.json is the data contract."""
import argparse
import datetime as dt
import hashlib
import http.client
import json
import math
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

SCHEMA_VERSION = 1
DEFAULT_CONFIG = Path(__file__).parent / 'resources' / 'clusters.yml'
ATOM = '{http://www.w3.org/2005/Atom}'
ARXIV = '{http://arxiv.org/schemas/atom}'
OPENSEARCH = '{http://a9.com/-/spec/opensearch/1.1/}'
ARXIV_PATTERN = re.compile(r'(?<![\w.])((?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7}))(?:v(\d+))?(?![\d.])', re.I)


def utc_yesterday():
    return dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)


def atomic_text(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(path.parent), prefix='.' + path.name + '.', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def atomic_json(path, value):
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load_config(path):
    path = Path(path)
    with path.open(encoding='utf-8') as stream:
        if path.suffix.lower() == '.json':
            cfg = json.load(stream)
        else:
            try:
                import yaml
            except ImportError as exc:
                raise ValueError('YAML configuration requires PyYAML; install project dependencies.') from exc
            try:
                cfg = yaml.safe_load(stream)
            except yaml.YAMLError as exc:
                raise ValueError('Invalid YAML configuration: ' + str(exc)) from exc
    if not isinstance(cfg, dict) or not isinstance(cfg.get('clusters'), dict):
        raise ValueError('Configuration must contain a clusters mapping.')
    for name, definition in cfg['clusters'].items():
        if not isinstance(name, str) or not isinstance(definition, dict):
            raise ValueError('Every cluster needs a string name and a mapping.')
        for field in ('include', 'exclude'):
            values = definition.get(field, []) or []
            if not isinstance(values, list) or not all(isinstance(v, str) and v.strip() for v in values):
                raise ValueError('Cluster %s.%s must be a list of nonempty strings.' % (name, field))
    cfg.setdefault('scoring', {})
    cfg.setdefault('sources', {})
    if not isinstance(cfg['scoring'], dict) or not isinstance(cfg['sources'], dict):
        raise ValueError('scoring and sources must be mappings.')
    for key, value in cfg['scoring'].items():
        if key == 'hype_words':
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError('scoring.hype_words must be a string list.')
        elif not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError('Scoring weight %s must be numeric.' % key)
    for name, source in cfg['sources'].items():
        if not isinstance(source, dict):
            raise ValueError('Source %s must be a mapping.' % name)
    categories = cfg['sources'].get('arxiv', {}).get('categories', ['cs.AI'])
    if not isinstance(categories, list) or not categories or not all(isinstance(v, str) and v.strip() for v in categories):
        raise ValueError('sources.arxiv.categories must be a nonempty string list.')
    return cfg


def arxiv_id(value):
    match = ARXIV_PATTERN.search(str(value or ''))
    return match.group(1).lower() if match else ''


def _strings(values):
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple)):
        raise ValueError('Expected a string or list of strings.')
    return list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


def normalize_paper(raw, source=None):
    if not isinstance(raw, dict):
        raise ValueError('Every paper must be an object.')
    title = str(raw.get('title') or '').strip()
    if not title:
        raise ValueError('Paper title is required.')
    url = str(raw.get('url') or raw.get('link') or '')
    raw_id = str(raw.get('id') or '')
    aid = arxiv_id(raw_id) or arxiv_id(url)
    paper_id = 'arxiv:' + aid if aid else raw_id
    if not paper_id:
        identity = url or re.sub(r'\s+', ' ', title).lower()
        paper_id = 'external:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:24]
    authors = []
    raw_authors = raw.get('authors') or []
    if not isinstance(raw_authors, list):
        raise ValueError('Paper authors must be a list.')
    for author in raw_authors:
        if isinstance(author, str):
            authors.append({'name': author, 'affiliations': []})
        elif isinstance(author, dict) and author.get('name'):
            authors.append({'name': str(author['name']), 'affiliations': sorted(_strings(author.get('affiliations', author.get('affiliation'))))})
        else:
            raise ValueError('Each author must have a name.')
    sources = _strings(raw.get('sources', raw.get('source')))
    if source and source not in sources:
        sources.append(source)
    try:
        votes = max(0, int(raw.get('hf_upvotes', raw.get('upvotes', 0)) or 0))
    except (ValueError, TypeError) as exc:
        raise ValueError('HF upvotes must be an integer.') from exc
    return {
        'id': paper_id, 'title': title, 'abstract': str(raw.get('abstract') or raw.get('summary') or ''),
        'authors': authors, 'url': url or ('https://arxiv.org/abs/' + aid if aid else ''),
        'pdf_url': str(raw.get('pdf_url') or ('https://arxiv.org/pdf/' + aid if aid else '')),
        'published': str(raw.get('published') or raw.get('publishedAt') or ''), 'updated': str(raw.get('updated') or ''),
        'sources': sorted(sources), 'hf_upvotes': votes, 'hf_featured_dates': sorted(_strings(raw.get('hf_featured_dates'))),
        'clusters': [], 'primary_cluster': None, 'prior': 0.0, 'prior_signals': [], 'change': 'new',
    }


def parse_arxiv_xml(payload):
    root = ET.fromstring(payload)
    if root.tag not in (ATOM + 'feed', 'feed'):
        raise ValueError('arXiv did not return an Atom feed.')
    ns = ATOM if root.tag == ATOM + 'feed' else ''
    total_node = root.find(OPENSEARCH + 'totalResults')
    total = int(total_node.text) if total_node is not None and total_node.text else None
    papers = []
    for entry in root.findall(ns + 'entry'):
        def content(name):
            return ' '.join((entry.findtext(ns + name) or '').split())
        link = content('id')
        if '/api/errors' in link:
            raise ValueError('arXiv API error: ' + content('summary'))
        authors = []
        for author in entry.findall(ns + 'author'):
            authors.append({'name': (author.findtext(ns + 'name') or '').strip(), 'affiliations': [' '.join((a.text or '').split()) for a in author.findall(ARXIV + 'affiliation')]})
        pdf = ''
        for item in entry.findall(ns + 'link'):
            if item.get('title') == 'pdf' or item.get('type') == 'application/pdf':
                pdf = item.get('href', '')
        papers.append(normalize_paper({'title': content('title'), 'abstract': content('summary'), 'url': link, 'pdf_url': pdf, 'authors': authors, 'published': content('published'), 'updated': content('updated')}, 'arxiv'))
    return papers, total


class FetchError(Exception):
    def __init__(self, message, attempts):
        super().__init__(message)
        self.attempts = attempts


class HttpClient:
    """Bounded retries and spacing; all request attempts enter diagnostics."""
    def __init__(self, timeout=30, retries=2, interval=3.0):
        self.timeout, self.retries, self.interval = timeout, retries, interval
        self.last_request = None

    def get(self, url):
        for attempt in range(self.retries + 1):
            if self.last_request is not None:
                delay = self.interval - (time.monotonic() - self.last_request)
                if delay > 0:
                    time.sleep(delay)
            self.last_request = time.monotonic()
            request = urllib.request.Request(url, headers={'User-Agent': 'research-radar/1.0 (paper discovery)', 'Accept': 'application/atom+xml, application/json'})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return response.read(), attempt + 1
            except (OSError, urllib.error.URLError, http.client.HTTPException) as exc:
                retryable = not isinstance(exc, urllib.error.HTTPError) or exc.code in (408, 429, 500, 502, 503, 504)
                if attempt >= self.retries or not retryable:
                    raise FetchError(str(exc), attempt + 1) from exc
                delay = min(30.0, 2.0 ** attempt)
                if isinstance(exc, urllib.error.HTTPError) and exc.headers:
                    try:
                        delay = min(30.0, max(delay, float(exc.headers.get('Retry-After', '0'))))
                    except (TypeError, ValueError):
                        pass
                time.sleep(delay)


def source_result(name):
    return {'name': name, 'status': 'success', 'count': 0, 'attempts': 0, 'errors': [], 'truncated': False}


def _failure(result, error, papers):
    result['errors'].append(str(error))
    result['status'] = 'partial' if papers else 'failed'
    result['count'] = len(papers)
    if isinstance(error, FetchError):
        result['attempts'] += error.attempts


def fetch_arxiv(cfg, start, end, client, max_results):
    result, papers = source_result('arxiv'), []
    settings = cfg.get('sources', {}).get('arxiv', {})
    categories = settings.get('categories', ['cs.AI', 'cs.LG', 'cs.RO', 'cs.CV', 'cs.CL', 'cs.MA', 'cs.HC', 'cs.NE'])
    base = settings.get('base_url', 'https://export.arxiv.org/api/query')
    query = '(%s) AND submittedDate:[%s0000 TO %s2359]' % (' OR '.join('cat:' + str(c) for c in categories), start.strftime('%Y%m%d'), end.strftime('%Y%m%d'))
    try:
        while len(papers) < max_results:
            page_size = min(200, max_results - len(papers))
            params = urllib.parse.urlencode({'search_query': query, 'start': len(papers), 'max_results': page_size, 'sortBy': 'submittedDate', 'sortOrder': 'descending'})
            payload, attempts = client.get(base + ('&' if '?' in base else '?') + params)
            result['attempts'] += attempts
            page, total = parse_arxiv_xml(payload)
            papers.extend(page[:page_size])
            if len(page) > page_size:
                result['truncated'] = True
                break
            if total is not None:
                result['available_count'] = total
            if not page or len(page) < page_size:
                if total is not None and len(papers) < total:
                    result['truncated'] = True
                    result['errors'].append('arXiv returned a short page before its advertised total was reached.')
                break
            if total is not None and len(papers) >= total:
                break
            if len(papers) >= max_results:
                result['truncated'] = True
                break
    except (FetchError, ValueError, ET.ParseError, TypeError) as exc:
        _failure(result, exc, papers)
    result['count'] = len(papers)
    if result['truncated'] and result['status'] == 'success':
        result['status'] = 'partial'
    return papers, result


def fetch_hf(cfg, start, end, client, max_results):
    result, papers = source_result('hf'), []
    base = cfg.get('sources', {}).get('huggingface_daily', {}).get('url', 'https://huggingface.co/api/daily_papers')
    current = start
    while current <= end:
        try:
            payload, attempts = client.get(base + ('&' if '?' in base else '?') + urllib.parse.urlencode({'date': current.isoformat()}))
            result['attempts'] += attempts
            data = json.loads(payload)
            if not isinstance(data, list):
                raise ValueError('HF daily response must be an array.')
            for item in data:
                raw = item.get('paper', {})
                pid = raw.get('id', '')
                paper = normalize_paper({'id': pid, 'title': raw.get('title'), 'abstract': raw.get('summary'), 'authors': raw.get('authors', []), 'published': raw.get('publishedAt', ''), 'url': 'https://arxiv.org/abs/' + pid if arxiv_id(pid) else 'https://huggingface.co/papers/' + pid, 'hf_upvotes': raw.get('upvotes', item.get('upvotes', 0)), 'hf_featured_dates': [current.isoformat()]}, 'hf')
                if len(papers) >= max_results:
                    result['truncated'] = True
                    break
                papers.append(paper)
            if result['truncated'] or (len(papers) >= max_results and current < end):
                result['truncated'] = True
                break
        except (FetchError, ValueError, TypeError, AttributeError) as exc:
            _failure(result, exc, papers)
            result['errors'][-1] = current.isoformat() + ': ' + result['errors'][-1]
        current += dt.timedelta(days=1)
    result['count'] = len(papers)
    if result['errors']:
        result['status'] = 'partial' if papers else 'failed'
    elif result['truncated']:
        result['status'] = 'partial'
    return papers, result


def import_file(path, max_results):
    result, papers = source_result('file'), []
    try:
        with Path(path).open(encoding='utf-8') as stream:
            data = json.load(stream)
        if isinstance(data, dict):
            if data.get('schema_version', 1) != SCHEMA_VERSION:
                raise ValueError('Unsupported paper schema_version.')
            input_run = data.get('run')
            if input_run is not None:
                if not isinstance(input_run, dict):
                    raise ValueError('Input run must be an object.')
                upstream_sources = input_run.get('sources', [])
                if not isinstance(upstream_sources, list) or not all(isinstance(s, (dict, str)) for s in upstream_sources):
                    raise ValueError('Input run sources must be an array of objects or legacy source names.')
                # Importing a file cannot repair a previous collection gap. Keep
                # the entire run, including every source diagnostic, unchanged.
                result['input_run'] = input_run
                if input_run.get('status') in ('partial', 'failed'):
                    result['errors'].append('Imported run coverage is ' + input_run['status'] + '; preserved in input_run.')
                for upstream in upstream_sources:
                    if isinstance(upstream, str):
                        continue
                    if upstream.get('status') in ('partial', 'failed') or upstream.get('truncated'):
                        result['errors'].append('Upstream source %s: status=%s; truncated=%s; errors=%s' % (
                            upstream.get('name', 'unknown'), upstream.get('status', 'unknown'),
                            bool(upstream.get('truncated')), json.dumps(upstream.get('errors', []), ensure_ascii=False)))
            data = data.get('papers')
        if not isinstance(data, list):
            raise ValueError('Input must be a paper array or an object containing papers.')
        result['available_count'] = len(data)
        result['truncated'] = len(data) > max_results
        for index, raw in enumerate(data[:max_results]):
            try:
                papers.append(normalize_paper(raw, 'file'))
            except (ValueError, TypeError) as exc:
                result['errors'].append('Record %d: %s' % (index + 1, exc))
    except (OSError, ValueError, TypeError) as exc:
        _failure(result, exc, papers)
    result['count'] = len(papers)
    if result['errors']:
        result['status'] = 'partial' if papers else 'failed'
    elif result['truncated']:
        result['status'] = 'partial'
    return papers, result


def keyword_match(keyword, text):
    parts = re.split(r'[\s_-]+', keyword.strip())
    pattern = r'(?<!\w)' + r'[\s_-]+'.join(re.escape(p) for p in parts) + r'(?!\w)'
    return re.search(pattern, text, re.IGNORECASE) is not None


def matching_clusters(paper, clusters):
    text = paper['title'] + ' ' + paper['abstract']
    return [name for name, definition in clusters.items() if any(keyword_match(k, text) for k in definition.get('include', []) or []) and not any(keyword_match(k, text) for k in definition.get('exclude', []) or [])]


def score_paper(paper, weights):
    """Observable text features only: a prior is never a verified quality score."""
    text = paper['title'] + ' ' + paper['abstract']
    lower = text.lower()
    value, signals = 0.0, []
    def add(label, key, default):
        nonlocal value
        amount = float(weights.get(key, default))
        value += amount
        signals.append('%s (%+g)' % (label, amount))
    promised = re.search(r'\b(?:will|plan to|intend to)\s+(?:be\s+)?(?:release|released|publish|open.source)\b', lower)
    available = re.search(r'\bcode\s+(?:is\s+)?(?:publicly\s+)?available\b|github\.com/|huggingface\.co/', lower)
    if promised:
        signals.append('code_release_promised (unverified)')
    elif available and not re.search(r'\bcode\s+(?:is\s+)?not\s+(?:yet\s+)?available\b', lower):
        add('code_available_claim', 'code_released', 3.0)
    benchmarks = ('COCO', 'ImageNet', 'AGIBench', 'BridgeV2', 'LIBERO', 'CALVIN', 'RLBench', 'RoboCasa', 'BEHAVIOR', 'MT-Bench', 'HumanEval', 'SWE-Bench', 'GSM8K', 'MATH', 'ARC-AGI', 'MMLU', 'MMBench', 'VideoMME', 'EgoSchema', 'Open-X-Embodiment', 'DROID', 'TongVerse')
    found = [b for b in benchmarks if keyword_match(b, text)]
    if found:
        add('benchmark_mentioned:' + ','.join(found), 'benchmark_named', 1.5)
    baselines = ('LLaVA', 'Qwen', 'DeepSeek', 'LLaMA', 'pi-0', 'OpenVLA', 'RT-2', 'diffusion policy', 'RDT', 'GR00T', 'RoboVLM')
    found = [b for b in baselines if keyword_match(b, text)]
    if re.search(r'(?<!\w)ACT(?!\w)', text):
        found.append('ACT')
    if found:
        add('baseline_mentioned:' + ','.join(found), 'baseline_named', 1.5)
    if keyword_match('ablation', text) or keyword_match('ablations', text):
        add('ablation_mentioned', 'ablation_present', 1.0)
    numbers = bool(re.search(r'(?<!\w)\d+(?:[.,]\d+)?(?:\s*[%×x])?(?!\w)', paper['abstract']))
    if not numbers:
        add('no_numeric_evidence_in_abstract', 'no_numbers', -1.5)
        if re.search(r'state[- ]of[- ]the[- ]art', lower):
            add('sota_claim_without_numbers', 'vague_sota_claim', -2.0)
    for word in weights.get('hype_words', []):
        if keyword_match(word, text):
            add('hype_term:' + word, 'hype_penalty', -1.0)
    buzz = ('novel', 'innovative', 'comprehensive', 'extensive', 'remarkable', 'promising', 'powerful', 'effective', 'robust')
    count = sum(len(re.findall(r'\b' + word + r'\b', lower)) for word in buzz)
    if count / max(1, len(paper['abstract'].split())) > 0.05:
        add('high_buzzword_density', 'buzzword_density', -2.0)
    if paper['hf_upvotes'] >= 50:
        add('hf_upvotes_ge_50', 'hf_popular', 3.0)
    elif paper['hf_upvotes'] >= 10:
        add('hf_upvotes_ge_10', 'hf_noticed', 1.5)
    return round(value, 2), signals


def _version(paper):
    match = ARXIV_PATTERN.search(paper.get('url', ''))
    return int(match.group(2) or 0) if match else 0


def merge_papers(first, second):
    """Keep newest version and preserve affiliations and source observations."""
    first_key = (_version(first), first.get('updated') or '', 'arxiv' in first['sources'])
    second_key = (_version(second), second.get('updated') or '', 'arxiv' in second['sources'])
    preferred, other = (second, first) if second_key >= first_key else (first, second)
    merged = dict(preferred)
    for key in ('title', 'abstract', 'authors', 'url', 'pdf_url', 'published', 'updated'):
        if not merged.get(key):
            merged[key] = other.get(key)
    lookup = {a['name'].casefold(): a for a in other.get('authors', [])}
    merged['authors'] = [{'name': a['name'], 'affiliations': sorted(set(a['affiliations'] + lookup.get(a['name'].casefold(), {}).get('affiliations', [])))} for a in merged['authors']]
    merged['sources'] = sorted(set(first['sources'] + second['sources']))
    merged['hf_featured_dates'] = sorted(set(first['hf_featured_dates'] + second['hf_featured_dates']))
    merged['hf_upvotes'] = max(first['hf_upvotes'], second['hf_upvotes'])
    return merged


def fingerprint(paper):
    fields = ('id', 'title', 'abstract', 'authors', 'url', 'pdf_url', 'published', 'updated', 'sources', 'hf_upvotes', 'hf_featured_dates')
    value = {field: paper[field] for field in fields}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def prepare_papers(raw_papers, previous, cfg):
    observed = {}
    for paper in raw_papers:
        observed[paper['id']] = merge_papers(observed[paper['id']], paper) if paper['id'] in observed else paper
    output = []
    for paper_id, paper in observed.items():
        old = previous.get(paper_id)
        if old:
            paper = merge_papers(old, paper)
        paper['change'] = 'new' if old is None else ('seen' if fingerprint(old) == fingerprint(paper) else 'updated')
        paper['clusters'] = matching_clusters(paper, cfg['clusters'])
        paper['primary_cluster'] = paper['clusters'][0] if paper['clusters'] else None
        paper['prior'], paper['prior_signals'] = score_paper(paper, cfg['scoring'])
        output.append(paper)
    return sorted(output, key=lambda paper: (-paper['prior'], paper['id']))


def _md(value):
    return str(value).replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('<', '&lt;').replace('>', '&gt;')


def render_report(run, papers, cfg, cluster, top, show_all):
    lines = ['# Paper Sweep — %s → %s' % (run['window']['start'], run['window']['end']), '', '- Run: `%s` · Status: **%s** · %d unique papers' % (run['id'], run['status'], len(papers)), '- Dates use UTC. HF dates indicate featuring; publication dates remain separate.', '- Prior scores are unverified text signals. Full records, including unmatched papers, are in papers.json.', '']
    for source in run['sources']:
        lines.append('- Source %s: %s; %d records; truncated=%s' % (source['name'], source['status'], source['count'], source['truncated']))
        for error in source['errors']:
            lines.append('  - Error: ' + _md(error))
    names = list(cfg['clusters']) + ['Unclassified'] if cluster == 'all' else [cluster]
    for name in names:
        selected = [p for p in papers if (not p['clusters'] if name == 'Unclassified' else name in p['clusters'])]
        shown = selected if show_all else selected[:top]
        lines.extend(['', '## %s (%d shown / %d matched)' % (name, len(shown), len(selected)), ''])
        if not shown:
            lines.append('_No candidates._')
        for paper in shown:
            authors = '; '.join(_md(a['name']) + (' (' + _md(', '.join(a['affiliations'])) + ')' if a['affiliations'] else '') for a in paper['authors'])
            lines.extend(['### %s `prior=%s`' % (_md(paper['title']), paper['prior']), '', '- ID: `%s` · Change: %s' % (paper['id'], paper['change']), '- Authors: ' + authors, '- Published: %s · Updated: %s' % (paper['published'] or 'unknown', paper['updated'] or 'unknown'), '- Sources: ' + ', '.join(paper['sources']), '- Link: ' + paper['url'], '- Clusters: ' + (', '.join(paper['clusters']) or 'Unclassified'), '- HF upvotes: %d · Featured: %s' % (paper['hf_upvotes'], ', '.join(paper['hf_featured_dates']) or 'not observed'), '- Prior signals: ' + ('; '.join(paper['prior_signals']) or 'none'), '- Abstract: ' + _md(paper['abstract']), ''])
    return '\n'.join(lines) + '\n'


def _positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError('must be at least 1')
    return number


def _nonnegative(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return number


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', type=dt.date.fromisoformat, default=utc_yesterday(), help='UTC end date; default yesterday')
    parser.add_argument('--days', type=_positive, default=1)
    parser.add_argument('--source', choices=('arxiv', 'hf', 'both', 'file'), default='both')
    parser.add_argument('--input', type=Path, help='structured paper JSON for --source file')
    parser.add_argument('--cluster', default='all', help='Markdown view only; machine data always retains all papers')
    parser.add_argument('--top', type=_positive, default=15, help='maximum papers per Markdown cluster')
    parser.add_argument('--all', action='store_true', help='show all papers in the Markdown view')
    parser.add_argument('--out', type=Path, help='custom Markdown path')
    parser.add_argument('--data-dir', type=Path, default=Path.cwd() / 'data')
    parser.add_argument('--config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--max-results', type=_positive, default=4000, help='record cap per source; truncation is reported')
    parser.add_argument('--timeout', type=_positive, default=30, help='seconds per HTTP request')
    parser.add_argument('--retries', type=_nonnegative, default=2, help='additional attempts per request')
    return parser


def main(argv=None):
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.source == 'file' and args.input is None:
            parser.error('--source file requires --input')
        if args.source != 'file' and args.input is not None:
            parser.error('--input is only valid with --source file')
        cfg = load_config(args.config)
        if args.cluster != 'all' and args.cluster not in cfg['clusters']:
            parser.error('unknown cluster: ' + args.cluster)
    except SystemExit as exc:
        code = int(exc.code or 0)
        if code:
            print(json.dumps({'status': 'invalid_args', 'paper_count': 0, 'paths': {}, 'exit_code': code}))
        return code
    except (OSError, ValueError) as exc:
        print(json.dumps({'status': 'invalid_args', 'error': str(exc), 'paper_count': 0, 'paths': {}, 'exit_code': 2}))
        return 2
    paths = {}
    try:
        start = args.date - dt.timedelta(days=args.days - 1)
        data_dir = args.data_dir.resolve()
        state_path = data_dir / 'state' / 'papers.json'
        previous = {}
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding='utf-8'))
            if not isinstance(state, dict) or state.get('schema_version') != SCHEMA_VERSION or not isinstance(state.get('papers'), dict):
                raise ValueError('Invalid persistent state; refusing to overwrite it.')
            previous = state['papers']
            for key, paper in previous.items():
                if not isinstance(paper, dict) or paper.get('id') != key:
                    raise ValueError('Invalid persistent paper record: ' + str(key))
                fingerprint(paper)
        client = HttpClient(args.timeout, args.retries)
        raw_papers, sources = [], []
        input_run = None
        if args.source in ('arxiv', 'both'):
            papers, result = fetch_arxiv(cfg, start, args.date, client, args.max_results)
            raw_papers.extend(papers)
            sources.append(result)
        if args.source in ('hf', 'both'):
            papers, result = fetch_hf(cfg, start, args.date, client, args.max_results)
            raw_papers.extend(papers)
            sources.append(result)
        if args.source == 'file':
            raw_papers, result = import_file(args.input, args.max_results)
            input_run = result.pop('input_run', None)
            sources.append(result)
        status, exit_code = 'success', 0
        if all(s['status'] == 'failed' for s in sources):
            status, exit_code = 'failed', 1
        elif any(s['status'] != 'success' for s in sources):
            status, exit_code = 'partial', 2
        papers = prepare_papers(raw_papers, previous, cfg)
        now = dt.datetime.now(dt.timezone.utc)
        run_id = now.strftime('%Y%m%dT%H%M%S%fZ') + '-' + uuid.uuid4().hex[:8]
        run_dir = data_dir / 'runs' / run_id
        run = {'id': run_id, 'status': status, 'created_at': now.isoformat(), 'window': {'start': start.isoformat(), 'end': args.date.isoformat()}, 'sources': sources, 'paper_count': len(papers), 'exit_code': exit_code}
        if input_run is not None:
            run['input_run'] = input_run
        report_path = args.out.resolve() if args.out else run_dir / 'report.md'
        paths = {'papers': str(run_dir / 'papers.json'), 'run': str(run_dir / 'run.json'), 'report': str(report_path), 'state': str(state_path)}
        if report_path in (Path(paths['papers']), Path(paths['run']), state_path) or report_path.suffix.lower() == '.json':
            raise ValueError('--out must be a Markdown path distinct from machine data.')
        atomic_json(paths['papers'], {'schema_version': SCHEMA_VERSION, 'run': run, 'papers': papers})
        atomic_text(report_path, render_report(run, papers, cfg, args.cluster, args.top, args.all))
        if status != 'failed':
            updated = dict(previous)
            updated.update({p['id']: p for p in papers})
            atomic_json(state_path, {'schema_version': SCHEMA_VERSION, 'papers': updated})
        atomic_json(paths['run'], {'schema_version': SCHEMA_VERSION, **run, 'paths': paths})
        print(json.dumps({'status': status, 'paper_count': len(papers), 'paths': paths, 'exit_code': exit_code}, ensure_ascii=False))
        return exit_code
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc), 'paper_count': 0, 'paths': paths, 'exit_code': 1}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    sys.exit(main())
