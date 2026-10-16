"""Bounded literal YAML subset for CI metadata; unsupported syntax stays opaque."""
import copy
import json
import re

class Unsupported(ValueError):pass
class Reference(list):pass


def split(text,delimiter=','):
    result=[];start=0;quote=None;level=0;i=0
    while i<len(text):
        c=text[i]
        if quote:
            if c=='\\' and quote=='"':i+=2;continue
            if c==quote:
                if quote=="'" and i+1<len(text) and text[i+1]=="'":i+=2;continue
                quote=None
        elif c in "'\"":quote=c
        elif c in '[{':level+=1
        elif c in ']}':level-=1
        elif level==0 and c==delimiter:
            if delimiter!=':' or i+1==len(text) or text[i+1].isspace():
                result.append(text[start:i].strip());start=i+1
                if delimiter==':':return result+[text[start:].strip()]
        i+=1
    if quote or level:raise Unsupported('unclosed YAML scalar/collection')
    return result+[text[start:].strip()]


def uncomment(text):
    quote=None;i=0
    while i<len(text):
        c=text[i]
        if c=='\\' and quote=='"':i+=2;continue
        if c in "'\"":
            if quote==c:
                if c=="'" and i+1<len(text) and text[i+1]==c:i+=2;continue
                quote=None
            elif quote is None:quote=c
        if c=='#' and quote is None and (i==0 or text[i-1].isspace()):return text[:i].rstrip()
        i+=1
    return text.rstrip()


class Reader:
    def __init__(self,lines,anchors):self.lines=lines;self.anchors=anchors

    def scalar(self,text,depth=0):
        if depth>40:raise Unsupported('YAML nesting limit')
        if text.startswith('!reference '):
            value=self.scalar(text[11:].strip(),depth+1)
            if not isinstance(value,list):raise Unsupported('invalid reference')
            return Reference(value)
        if text.startswith('!'):raise Unsupported('unsupported YAML tag')
        if text.startswith('*'):
            if not re.fullmatch(r'\*[\w-]+',text) or text[1:] not in self.anchors:raise Unsupported('unknown YAML alias')
            return copy.deepcopy(self.anchors[text[1:]])
        if text.startswith('['):
            if not text.endswith(']'):raise Unsupported('multiline flow collection')
            return [self.scalar(v,depth+1) for v in split(text[1:-1]) if v]
        if text.startswith('{'):
            if not text.endswith('}'):raise Unsupported('multiline flow collection')
            result={}
            for item in filter(None,split(text[1:-1])):
                pair=split(item,':')
                if len(pair)!=2:raise Unsupported('unsupported flow mapping')
                result[str(self.scalar(pair[0],depth+1))]=self.scalar(pair[1],depth+1)
            return result
        if text.startswith('"'):
            try:return json.loads(text)
            except ValueError as exc:raise Unsupported('unsupported quoted scalar') from exc
        if text.startswith("'"):
            if not text.endswith("'"):raise Unsupported('multiline quoted scalar')
            return text[1:-1].replace("''","'")
        if text in {'true','false'}:return text=='true'
        if text in {'null','~'}:return None
        return text

    def value(self,text,index,indent,depth):
        anchor=None
        match=re.match(r'^&([\w-]+)(?:\s+(.*))?$',text)
        if match:anchor=match[1];text=match[2] or ''
        if re.fullmatch(r'[|>][+-]?\d?',text):
            chunks=[]
            while index<len(self.lines) and self.lines[index][0]>indent:
                chunks.append(self.lines[index][1]);index+=1
            value='\n'.join(chunks)
        elif text:
            value=self.scalar(text)
            if index<len(self.lines) and self.lines[index][0]>indent:
                if not isinstance(value,str) or text.startswith(('"',"'",'[','{','!','*')):raise Unsupported('unsupported multiline scalar')
                chunks=[value]
                while index<len(self.lines) and self.lines[index][0]>indent:
                    continuation=self.lines[index][1]
                    if continuation.startswith('- ') or len(split(continuation,':'))!=1:raise Unsupported('ambiguous multiline plain scalar')
                    chunks.append(continuation);index+=1
                value=' '.join(chunks)
        elif index<len(self.lines) and self.lines[index][0]>indent:value,index=self.block(index,self.lines[index][0],depth+1)
        elif index<len(self.lines) and self.lines[index][0]==indent and self.lines[index][1].startswith('- '):value,index=self.block(index,indent,depth+1,True)
        else:value=None
        if anchor:self.anchors[anchor]=copy.deepcopy(value)
        return value,index

    def block(self,index,indent,depth=0,indentless=False):
        if depth>40:raise Unsupported('YAML nesting limit')
        sequence=self.lines[index][1].startswith('- ');result=[] if sequence else {}
        while index<len(self.lines) and self.lines[index][0]==indent:
            raw=self.lines[index][1]
            if sequence and indentless and not raw.startswith('- '):break
            index+=1
            if sequence:
                if not raw.startswith('- '):raise Unsupported('mixed YAML collection')
                raw=raw[2:].strip();pair=split(raw,':') if not raw.startswith(('"',"'",'[','{','!')) else [raw]
                if len(pair)==2:
                    key=self.scalar(pair[0]);value,index=self.value(pair[1],index,indent+2,depth)
                    row={key:value}
                    if index<len(self.lines) and self.lines[index][0]>indent:
                        extra,index=self.block(index,self.lines[index][0],depth+1)
                        if not isinstance(extra,dict):raise Unsupported('invalid sequence mapping')
                        row.update(extra)
                    result.append(row)
                else:
                    value,index=self.value(raw,index,indent,depth);result.append(value)
            else:
                pair=split(raw,':')
                if len(pair)!=2:raise Unsupported('unsupported mapping entry')
                key=self.scalar(pair[0]);value,index=self.value(pair[1],index,indent,depth)
                if not isinstance(key,str):raise Unsupported('nonliteral YAML key')
                if key=='<<':
                    values=value if isinstance(value,list) else [value]
                    for row in reversed(values):
                        if not isinstance(row,dict):raise Unsupported('unsupported YAML merge')
                        for name,item in row.items():result.setdefault(name,item)
                else:
                    if key in result:raise Unsupported('duplicate YAML key')
                    result[key]=value
        if index<len(self.lines) and self.lines[index][0]>indent:raise Unsupported('unsupported YAML indentation')
        return result,index


def load(text):
    lines=[]
    for raw in text.splitlines():
        clean=uncomment(raw)
        if not clean.strip() or clean.strip() in {'---','...'}:continue
        if '\t' in clean[:len(clean)-len(clean.lstrip())]:raise Unsupported('tab YAML indentation')
        lines.append((len(clean)-len(clean.lstrip()),clean.strip()))
    if len(lines)>20000:raise Unsupported('CI file size limit')
    anchors={};reader=Reader(lines,anchors);result={};i=0
    while i<len(lines):
        if lines[i][0]!=0:raise Unsupported('invalid top-level indentation')
        start=i;i+=1
        while i<len(lines) and lines[i][0]>0:i+=1
        section=lines[start:i];key=split(section[0][1],':')
        if len(key)!=2:raise Unsupported('unsupported CI document')
        name=reader.scalar(key[0])
        try:
            local=Reader(section,anchors);parsed,end=local.block(0,0)
            if end!=len(section):raise Unsupported('incomplete CI section')
            if set(parsed)&set(result):raise Unsupported('duplicate top-level CI key')
            result.update(parsed)
        except Unsupported as exc:result[name]={'__unsupported__':str(exc)}
    return result
