"""Bounded three-valued GitLab rule expressions over a fixed declared profile."""
import json
import re

UNKNOWN=object()
PRESENT=object()
TOKEN=re.compile('\\s*(\\$[A-Za-z_][A-Za-z0-9_]*|&&|\\|\\||==|!=|[()]|"(?:[^"\\\\]|\\\\.)*"|\'[^\']*\')')
VALUES={'CI_PIPELINE_SOURCE':'merge_request_event','CI_COMMIT_BRANCH':None,'CI_MERGE_REQUEST_ID':PRESENT,
        'FASTLANE':None,'BRANCH_ENV_CLEANUP':None,'SONAR_SCAN':None}


def conjunction(a,b):
    return False if a is False or b is False else True if a is True and b is True else None


def disjunction(a,b):
    return True if a is True or b is True else False if a is False and b is False else None


class Parser:
    def __init__(self,text,profile):
        if not isinstance(text,str) or len(text)>8192:raise ValueError('expression size')
        self.tokens=[];offset=0;self.index=0;self.values=VALUES if profile=='merge_request_quality' else {}
        while offset<len(text):
            match=TOKEN.match(text,offset)
            if not match:raise ValueError('unsupported expression token')
            self.tokens.append(match[1]);offset=match.end()
            if len(self.tokens)>256:raise ValueError('expression token limit')

    def take(self):
        if self.index==len(self.tokens):raise ValueError('incomplete expression')
        token=self.tokens[self.index];self.index+=1;return token

    def peek(self):return self.tokens[self.index] if self.index<len(self.tokens) else None

    def atom(self,depth):
        if depth>32:raise ValueError('expression nesting')
        token=self.take()
        if token=='(':
            value=self.expression(depth+1)
            if self.take()!=')':raise ValueError('unclosed expression')
            return value
        if not token.startswith('$'):raise ValueError('variable operand required')
        value=self.values.get(token[1:],UNKNOWN)
        if self.peek() in {'==','!='}:
            op=self.take();literal=self.take()
            if not literal.startswith(('"',"'")):raise ValueError('literal comparison required')
            literal=json.loads(literal) if literal.startswith('"') else literal[1:-1]
            if value is UNKNOWN or value is PRESENT:return None
            equal=value==literal
            return equal if op=='==' else not equal
        return None if value is UNKNOWN else value is PRESENT or bool(value)

    def both(self,depth):
        value=self.atom(depth)
        while self.peek()=='&&':self.take();value=conjunction(value,self.atom(depth))
        return value

    def expression(self,depth=0):
        value=self.both(depth)
        while self.peek()=='||':self.take();value=disjunction(value,self.both(depth))
        return value


def evaluate(text,profile,strict=False):
    if text in (True,'true'):return True
    if text in (False,'false'):return False
    try:
        parser=Parser(text.strip(),profile);value=parser.expression()
        if parser.index!=len(parser.tokens):raise ValueError('unsupported trailing expression')
        return value
    except (ValueError,AttributeError,TypeError) as exc:
        if strict:raise ValueError('unsupported CI expression') from exc
        return None


def scheduling(value):
    return isinstance(value,dict) and not set(value)-{'on_new_commit','on_job_failure'} and all(
        isinstance(v,str) and v in ({'conservative','interruptible','none'} if k=='on_new_commit' else {'all','none'}) for k,v in value.items())
