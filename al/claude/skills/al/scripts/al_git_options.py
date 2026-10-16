"""Exact observational subcommand options; argument positions remain data."""

# Tables separate flags, required operands and optional attached long operands.
DIFF_FLAGS='--patch --no-patch --raw --patch-with-raw --patch-with-stat --numstat --shortstat --dirstat --cumulative --dirstat-by-file --check --summary --name-only --name-status --stat --compact-summary --binary --full-index --no-color --no-renames --no-prefix --relative --text --reverse --exit-code --quiet --no-ext-diff --no-textconv --ignore-space-at-eol --ignore-cr-at-eol --ignore-blank-lines --ignore-all-space --ignore-space-change --minimal --patience --histogram --word-diff --color-words --no-index --cached --staged'.split()
DIFF_VALUES='--diff-filter --word-diff-regex --find-object --src-prefix --dst-prefix --line-prefix --inter-hunk-context --stat-width --stat-name-width --stat-count --diff-algorithm --anchored --ignore-matching-lines'.split()
DIFF_OPTIONAL='--color --abbrev --unified --find-renames --find-copies --word-diff --color-moved --color-moved-ws --relative --stat --dirstat --dirstat-by-file'.split()
LOG_FLAGS='--oneline --all --branches --tags --remotes --reflog --no-walk --do-walk --follow --reverse --first-parent --merges --no-merges --boundary --left-right --cherry --cherry-pick --cherry-mark --graph --parents --children --full-history --simplify-merges --simplify-by-decoration --topo-order --date-order --author-date-order --no-notes --no-show-signature --show-signature --all-match --invert-grep --regexp-ignore-case --basic-regexp --extended-regexp --fixed-strings --perl-regexp'.split()
LOG_VALUES='--grep --author --committer --since --until --after --before --max-count --skip --format --date --encoding --grep-reflog'.split()
TABLES={
 'grep':('''--text --ignore-case --word-regexp --line-regexp --invert-match --full-name --line-number --column --files-with-matches --name-only --files-without-match --null --count --extended-regexp --basic-regexp --fixed-strings --perl-regexp --quiet --no-messages --cached --untracked --no-index --no-exclude-standard --exclude-standard --recurse-submodules --all-match --and --or --not --break --heading --show-function --function-context'''.split(),
         '--regexp --file --max-count --max-depth --threads --after-context --before-context --context'.split(),['--color'], 'aIiwvHhnNlLzocEGFPqpW', 'efmABC', ''),
 'diff':(DIFF_FLAGS,DIFF_VALUES,DIFF_OPTIONAL,'pustbwrRaBz','SGO','UMC'),
 'log':(DIFF_FLAGS+LOG_FLAGS,DIFF_VALUES+LOG_VALUES,DIFF_OPTIONAL+['--pretty','--decorate','--notes'], 'pustbwrRaBz','nSGLO','UMC'),
 'show':(DIFF_FLAGS+LOG_FLAGS,DIFF_VALUES+LOG_VALUES,DIFF_OPTIONAL+['--pretty','--decorate','--notes'], 'pustbwrRaBz','nSGLO','UMC'),
 'status':('--short --branch --show-stash --porcelain --long --verbose --null --ignored --no-renames'.split(),[],['--untracked-files','--ignore-submodules','--column','--porcelain','--find-renames','--ignored'],'sbvz','','u'),
 'ls-files':('--cached --deleted --modified --others --ignored --stage --unmerged --killed --directory --no-empty-directory --eol --full-name --error-unmatch --deduplicate --exclude-standard --debug --resolve-undo --recurse-submodules'.split(),['--exclude','--exclude-from','--exclude-per-directory','--with-tree','--format'],[],'ztvfcdomiuks','xX',''),
 'ls-tree':('--name-only --name-status --full-name --full-tree --long --object-only'.split(),['--format'],['--abbrev'],'drtzl','',''),
 'rev-parse':('--verify --quiet --revs-only --no-revs --flags --no-flags --symbolic --symbolic-full-name --abbrev-ref --show-toplevel --show-prefix --show-cdup --git-dir --absolute-git-dir --git-common-dir --is-inside-work-tree --is-inside-git-dir --is-bare-repository --show-object-format --all --branches --tags --remotes --not --sq --sq-quote --end-of-options'.split(),['--prefix','--default','--disambiguate','--since','--until'],['--short','--abbrev-ref'],'q','',''),
 'merge-base':('--all --octopus --independent --is-ancestor --fork-point'.split(),[],[],'a','',''),
 'blame':('--root --show-stats --progress --line-porcelain --porcelain --incremental --show-email --show-name --show-number --long --raw --first-parent --reverse --ignore-whitespace --no-progress'.split(),['--ignore-rev','--ignore-revs-file','--contents','--date'],['--abbrev'],'bcfelnpstw','LS','MC'),
 'describe':('--all --tags --contains --exact-match --always --long --first-parent'.split(),['--abbrev','--candidates','--match','--exclude'],['--dirty','--broken'],'','',''),
 'check-ignore':('--verbose --non-matching --no-index --stdin --quiet'.split(),[],[],'zvqn','',''),
}


def inspect(sub,tail,error):
    if sub not in TABLES:return
    flags,values,optional,short_flags,short_values,short_optional=TABLES[sub];i=0
    while i<len(tail):
        token=tail[i];i+=1
        if token=='--':return
        if not token.startswith('-') or token=='-':continue
        if sub in {'log','show'} and token[1:].isdigit():continue
        if token.startswith('--'):
            name,equals,value=token.partition('=')
            if name in values:
                if not equals:
                    if i>=len(tail):raise error('Git option argument missing: '+name)
                    i+=1
            elif name in optional:pass
            elif name not in flags or equals:raise error('unsupported Git '+sub+' option '+token)
            continue
        for pos,letter in enumerate(token[1:],1):
            if letter in short_values:
                if pos==len(token)-1:
                    if i>=len(tail):raise error('Git option argument missing: -'+letter)
                    i+=1
                break
            if letter in short_optional:break
            if letter not in short_flags:raise error('unsupported Git '+sub+' short option '+letter)
