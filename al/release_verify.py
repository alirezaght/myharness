#!/usr/bin/env python3
"""Validate the runtime payload and run the complete AL test suites for release CI."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from al.installer.manifest import LAYOUT, cache_release, load_layout, required, selected, verify_cache  # noqa: E402
from al.installer.source import TAG, git, resolve_release, safe_subdir, source_identity  # noqa: E402


def check_layout() -> dict:
    layout = load_layout()
    files: dict[str, tuple[int, int]] = {}
    for platform in layout["platforms"]:
        root = ROOT / platform
        if not root.is_dir():
            raise ValueError(f"missing platform source: {root}")
        for path in root.rglob("*"):
            relative = path.relative_to(ROOT).as_posix()
            if path.is_symlink():
                raise ValueError(f"symlink in runtime source: {relative}")
            if not path.is_file():
                continue
            classification = selected(relative, layout)
            if any(part in layout["exclude_parts"] for part in path.parts):
                if classification is not None:
                    raise ValueError(f"excluded path selected for runtime payload: {relative}")
                continue
            if classification:
                files[relative] = (path.stat().st_size, path.stat().st_mode & 0o777)
    missing = sorted(required(layout) - files.keys())
    if missing:
        raise ValueError("missing required runtime resources: " + ", ".join(missing))
    if any("/tests/" in name or "/__pycache__/" in name or name.endswith((".pyc", ".pyo"))
           for name in files):
        raise ValueError("tests or bytecode entered the runtime payload")
    return {
        "status": "PASS",
        "kind": "workspace-layout",
        "runtime_schema": layout["runtime_schema"],
        "platforms": list(layout["platforms"]),
        "files": len(files),
        "bytes": sum(size for size, _ in files.values()),
        "layout": str(LAYOUT),
    }


def _run(command: list[str], cwd: Path) -> None:
    result = subprocess.run(command, cwd=cwd, check=False)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command)


AL_COMPLETE_VERIFICATION_V1 = True


def full(*, tier='full', workers=8, cache_mode='cold', report_path=None, changed_paths=(), binding=None, profile=False, plan_only=False):
    from al.verification_plan import collect_suites, build_plan
    from al.verification_profile import capture_source_binding, read_json, write_json, digest
    from al.verification_runner import run
    root=ROOT.parent.resolve()
    binding=binding or capture_source_binding(root)
    report_path=Path(report_path) if report_path else Path(tempfile.mkdtemp(prefix='al-verification-'))/'report.json'
    if report_path.resolve()==root or root in report_path.resolve().parents:raise ValueError('verification reports must be outside source')
    artifact_root=report_path.parent/(report_path.stem+'-runs')
    artifact_root.mkdir(parents=True,exist_ok=True)
    from al.verification_lifecycle import require_finished_runs
    require_finished_runs(artifact_root)
    directory=Path(tempfile.mkdtemp(prefix='run-',dir=artifact_root))
    collections=collect_suites(root,{'artifact_root':str(directory)})
    declarations=read_json(ROOT/'verification-obligations.json')
    plan=build_plan(collections,declarations,tier=tier,changed_paths=list(changed_paths),binding=binding)
    write_json(directory/'plan.json',plan)
    if plan_only:return {'plan':plan,'plan_path':str(directory/'plan.json'),'report_path':str(report_path)}
    # Earlier node durations only order shards (longest first); they never select or skip work.
    history=report_path.parent/(report_path.stem+'-durations.json')
    try:
        durations=read_json(history)
        if not isinstance(durations,dict) or not all(isinstance(k,str) and type(v) in (int,float) and 0<=v<86400 for k,v in durations.items()):durations={}
    except (OSError,ValueError):durations={}
    result=run(plan,workers=workers,artifact_root=directory/'execution',cache_mode=cache_mode,durations=durations,profile=profile)
    observed={node:seconds for shard in result.get('shards',[]) for node,seconds in shard.get('durations',{}).items() if type(seconds) in (int,float)}
    if observed:write_json(history,{**durations,**observed})
    result.update(plan_path=str(directory/'plan.json'),plan_sha256=hashlib.sha256((directory/'plan.json').read_bytes()).hexdigest(),
                  collection_seconds=sum(c['collection_seconds'] for c in collections.values()),
                  selected_obligations=len(plan['execution_assignments']),collected_obligations=len(plan['collected_obligations']),
                  equivalent_obligations=len(plan['shared_equivalents']),tier=tier,report_path=str(report_path),
                  obligation_digest=digest(plan['collected_obligations']),environment_binding=plan['environment_binding'])
    return result


def _tag(version):
    if not version:
        raise ValueError('an explicit stable --version is required; release verification never selects latest')
    tag = version if version.startswith('v') else 'v' + version
    if not TAG.fullmatch(tag):
        raise ValueError('version must be a stable vMAJOR.MINOR.PATCH tag')
    return tag


def check_release(source, version, subdir='al'):
    tag = _tag(version)
    if not source:
        raise ValueError('--check-release requires --source LOCAL_REPOSITORY')
    identity = source_identity(source)
    if identity.startswith('https://'):
        raise ValueError('release verification requires an existing local Git source')
    release = resolve_release(identity, safe_subdir(subdir), tag)
    with tempfile.TemporaryDirectory(prefix='al-release-verify-', dir=Path(tempfile.gettempdir()).resolve()) as directory:
        cache, manifest = cache_release(release, Path(directory))
        verified = verify_cache(cache, manifest['digest'])
    return verified


def _workspace():
    try:
        root = Path(git(ROOT, 'rev-parse', '--show-toplevel').decode().strip())
        head = git(root, 'rev-parse', '--verify', 'HEAD').decode().strip()
        dirty = bool(git(root, 'status', '--porcelain', '--untracked-files=normal'))
        return {'source_identity': str(root), 'head': head, 'dirty': dirty}
    except ValueError:
        return {'source_identity': None, 'head': None, 'dirty': None}


def _full_context(args):
    if args.source is not None:
        raise ValueError('--full tests this checkout; --source is only supported with --check-release')
    before = _workspace()
    ref = os.environ.get('GITHUB_REF', '')
    if os.environ.get('GITHUB_REF_TYPE') == 'tag' and not ref.startswith('refs/tags/'):
        raise ValueError('missing or malformed CI tag reference')
    if os.environ.get('GITHUB_ACTIONS') == 'true' and not ref:
        raise ValueError('missing CI reference')
    ci_tag = None
    if ref.startswith('refs/tags/'):
        ci_tag = _tag(ref[len('refs/tags/'):])
        # A GitHub tag ref must itself carry the canonical stable tag spelling.
        if ref != 'refs/tags/' + ci_tag:
            raise ValueError('unsupported CI tag reference')
    elif ref and (not ref.startswith('refs/heads/') or not ref[len('refs/heads/'):]):
        raise ValueError('unsupported CI reference')
    explicit = _tag(args.version) if args.version is not None else None
    if ci_tag and explicit and ci_tag != explicit:
        raise ValueError('explicit version differs from CI tag')
    tag = ci_tag or explicit
    if not tag:
        if args.source_subdir is not None:
            raise ValueError('--source-subdir requires tagged verification')
        return before, None
    if not before['source_identity'] or not before['head']:
        raise ValueError('tagged full verification requires a Git checkout with HEAD')
    if before['dirty'] is not False:
        raise ValueError('tagged full verification requires clean tracked and untracked source')
    subdir = Path(ROOT).resolve().relative_to(Path(before['source_identity'])).as_posix()
    subdir = safe_subdir(subdir)
    if args.source_subdir is not None and safe_subdir(args.source_subdir) != subdir:
        raise ValueError('--full payload must be the tested checkout subdirectory')
    manifest = check_release(before['source_identity'], tag, subdir)
    if manifest['commit'] != before['head']:
        raise ValueError('selected tag commit differs from checkout HEAD')
    if ci_tag:
        sha = os.environ.get('GITHUB_SHA', '')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', sha) or sha != before['head']:
            raise ValueError('CI GITHUB_SHA must be the full selected tag commit and checkout HEAD')
    return before, manifest


def _recheck_binding(before, manifest):
    after = _workspace()
    if after != before or after['dirty'] is not False:
        raise ValueError('checkout HEAD, identity or cleanliness changed during suites')
    root, tag = Path(before['source_identity']), manifest['tag']
    obj = git(root, 'rev-parse', '--verify', 'refs/tags/' + tag).decode().strip()
    commit = git(root, 'rev-parse', '--verify', 'refs/tags/' + tag + '^{commit}').decode().strip()
    if obj != manifest['tag_object'] or commit != manifest['commit']:
        raise ValueError('selected tag identity changed during suites')
    return after


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--check-layout', action='store_true', help='audit workspace resources without a release claim')
    group.add_argument('--check-release', action='store_true', help='validate one exact committed local tag payload')
    group.add_argument('--full', action='store_true', help='run complete suites with tagged or development provenance')
    parser.add_argument('--source', help='existing local Git source for --check-release')
    parser.add_argument('--version', help='exact stable release version; never latest')
    parser.add_argument('--source-subdir', default=None)
    group.add_argument('--tier', choices=['fast', 'affected'], help='focused tier; never complete verification')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--cache-mode', choices=['cold', 'warm', 'disabled'], default='cold')
    parser.add_argument('--report')
    parser.add_argument('--changed', action='append', default=[])
    parser.add_argument('--profile', action='store_true')
    parser.add_argument('--plan-only', action='store_true')
    args = parser.parse_args(argv)
    started = time.monotonic()
    try:
        if args.check_release:
            manifest = check_release(args.source, args.version, args.source_subdir if args.source_subdir is not None else 'al')
            report = {'status': 'PASS', 'kind': 'tagged-payload', 'source_bytes': 'committed-tag', 'manifest': manifest}
        elif args.check_layout:
            if args.source is not None or args.version is not None or args.source_subdir is not None:
                raise ValueError('--check-layout audits only the workspace; use --check-release for tags')
            report = {'status': 'PASS', 'kind': 'workspace-layout', 'layout': check_layout()}
        else:
            from al.verification_profile import capture_source_binding, write_json
            if args.workers<1:raise ValueError('workers must be positive')
            before, manifest = _full_context(args)
            source=capture_source_binding(ROOT.parent)
            layout = check_layout()
            suites = full(tier=args.tier or 'full',workers=args.workers,cache_mode=args.cache_mode,report_path=args.report,
                          changed_paths=args.changed,binding=source,profile=args.profile,plan_only=args.plan_only)
            after = _recheck_binding(before, manifest) if manifest else _workspace()
            if capture_source_binding(ROOT.parent)!=source:raise ValueError('source content or membership changed during suites')
            if args.plan_only:
                report={'status':'PLAN','kind':'verification-plan',**suites}
            else:
                reconciliation=suites['reconciliation']
                complete=(reconciliation['complete'] if args.full else reconciliation['selected_success'])
                elapsed=time.monotonic()-started
                target_met=elapsed<=360 if args.full else None
                report = {'schema_version':1,'contract':'al-complete-verification-v1','status':'PASS' if complete and target_met is not False else 'FAIL',
                          'kind': 'tagged-release' if manifest else 'development','layout':layout,'verification':suites,
                          'source_binding':source,'context_before':before,'context_after':after,'command':list(argv) if argv is not None else sys.argv[1:],
                          'complete':bool(args.full and reconciliation['complete']),'elapsed_seconds':elapsed,
                          'performance_target_seconds':360 if args.full else None,'performance_target_met':target_met}
                if manifest:
                    report['manifest']=manifest;report['binding']='verified-before-and-after-suites'
                path=Path(suites['report_path']);write_json(path,report)
                report['elapsed_seconds']=time.monotonic()-started
                if args.full and report['elapsed_seconds']>360:
                    report.update(status='FAIL',performance_target_met=False)
                write_json(path,report)
                print(json.dumps({'status':report['status'],'complete':report['complete'],'report':str(path),
                                  'report_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'elapsed_seconds':report['elapsed_seconds'],
                                  'collected_obligations':suites['collected_obligations'],'shared_equivalents':suites['equivalent_obligations']},sort_keys=True))
                print(f"{reconciliation['executed_passed']} passed, {reconciliation['executed_failed']} failed, {reconciliation['executed_errors']} errors")
                return 0 if report['status']=='PASS' else 1
        print(json.dumps(report, indent=2, sort_keys=True))
    except (OSError, ValueError, SyntaxError, subprocess.SubprocessError) as exc:
        failure = {'status': 'FAIL', 'complete': False, 'error': str(exc),
                   'elapsed_seconds': time.monotonic() - started}
        if args.report and (args.full or args.tier):
            from al.verification_profile import write_json
            path = Path(args.report).resolve()
            root = ROOT.parent.resolve()
            if path != root and root not in path.parents:
                write_json(path, failure)
                failure['report'] = str(path)
        print(json.dumps(failure, indent=2))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
