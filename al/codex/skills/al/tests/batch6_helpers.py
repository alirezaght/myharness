"""Public recovery/gate setup for the final bounded runtime batch."""
import json
import sys
from pathlib import Path
from hook_regression_helpers import begin,invoke,finish_review
from test_hook_batch6 import _manifest,_source,_start
from batch5_helpers import state,comparison,second_round


def pause(cli,tmp,kind='interrupt'):
    file=tmp/'pause.json';file.write_text(json.dumps({'reason':'pause safely','resume_instructions':'revalidate obligations'}))
    return invoke(cli,kind,'--file',str(file))[1].strip()


def ruled(cli,tmp,repo):
    begin(cli,tmp,repo);(repo/'Makefile').write_text('lint-imports:\n\ttrue\n')
    file=tmp/'rules.json';file.write_text(json.dumps(_manifest(state(cli),rule=_source(repo,'Makefile'))))
    invoke(cli,'gates','plan','--file',str(file))


def replanned_approval(cli,tmp,repo):
    assert state(cli)['phase']=='PLANNING' and state(cli)['spec_status'] is None
    invoke(cli,'review-start',ok=False);invoke(cli,'phase','IMPLEMENTATION',ok=False)
    file=tmp/'replacement.json';file.write_text(json.dumps(_manifest(state(cli),rule=_source(repo,'Makefile'))))
    invoke(cli,'gates','plan','--file',str(file));invoke(cli,'spec-gate','SPEC_READY');invoke(cli,'phase','IMPLEMENTATION')
    invoke(cli,'writer','start','--id','replacement','--role','al-engineer','--files','svc/app.py')
    invoke(cli,'writer','finish','--id','replacement','--outcome','success');invoke(cli,'phase','VERIFICATION')
    invoke(cli,'evidence','run','--area','unit','--fresh');finish_review(cli,tmp)
    invoke(cli,'phase','FINAL_VERIFICATION');invoke(cli,'approved')


def gated(cli,tmp,repo):
    begin(cli,tmp,repo)
    file=tmp/'active.json';argv=[sys.executable,'-c',"print('gate passed')"]
    file.write_text(json.dumps(_manifest(state(cli),source=_source(repo,'svc/app.py'),active='loc',argv=argv)))
    invoke(cli,'gates','plan','--file',str(file));invoke(cli,'gates','run','--stage','base','--id','loc')
    invoke(cli,'gates','run','--stage','candidate','--id','loc')


def conflict(cli,tmp,repo):
    _start(cli,tmp,repo,classification='COMPLEX',designer='yes')
    invoke(cli,'phase','PSEUDOCODE');invoke(cli,'phase','PLAN_CONFLICT')
    file=tmp/'resolution.md';file.write_text('Architect resolves this exact occurrence.\n')
    invoke(cli,'conflict-resolved','--file',str(file),'--reason','accepted constraint')
    return file


def unfinished(cli,tmp,repo,phase):
    _start(cli,tmp,repo,classification='COMPLEX' if phase=='PSEUDOCODE' else 'SMALL',designer='yes' if phase=='PSEUDOCODE' else 'no')
    (repo/'Makefile').write_text('lint-imports:\n\ttrue\n')
    install_replacement(cli,tmp,repo)
    invoke(cli,'phase',phase)


def replacement(cli,tmp,repo,command=None):
    rule=_source(repo,'Makefile')
    m=_manifest(state(cli),rule=rule,source=rule,active='loc' if command else None,argv=command)
    file=tmp/'retained-replacement.json';file.write_text(json.dumps(m))
    return file


def install_replacement(cli,tmp,repo,command=None):
    file=replacement(cli,tmp,repo,command)
    invoke(cli,'gates','plan','--file',str(file))
    return file
