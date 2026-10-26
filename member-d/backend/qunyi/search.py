"""可替换本地检索适配层：字符 n-gram + 词汇扩展 + TF-IDF；不是向量语义模型。"""
import re, math
from collections import Counter
from .store import dump
ALIASES=[('志愿','公益','服务'),('报名','申请','报送'),('表格','纸质','材料','报名表'),('集合','地点','哪里'),('讲座','讲堂','培训')]
STOP={'那个','一下','帮我','请问','消息','通知','之前','查找','找到','前几','有没有','记得','好像','这个','什么','时候'}
def tokenize(text,expand=False):
    s=re.sub(r'[^\w\u4e00-\u9fff]','',text.lower())
    for x in STOP:s=s.replace(x,'')
    terms=[s[i:i+2] for i in range(max(0,len(s)-1))]
    terms+=re.findall(r'[a-z0-9_]{2,}',text.lower())
    if expand:
        for group in ALIASES:
            if any(x in text for x in group):terms.extend(group)
    return dict(Counter(terms))

class LocalSearch:
    name='local_ngram_tfidf_with_aliases'
    def index(self,store,row):
        meta=__import__('json').loads(row['metadata'])
        text=row['content']+' '+meta.get('title','')
        store.db.execute('INSERT OR REPLACE INTO search_index VALUES(?,?,?,?)',
            (row['id'],row['workspace'],dump(tokenize(text)),row['revision']))
    def search(self,store,workspace,query,conditions,limit=12,expanded=True):
        import json
        rows=store.rows('SELECT m.*,i.tokens FROM messages m JOIN search_index i ON m.id=i.message_id AND m.revision=i.revision WHERE m.workspace=? AND i.workspace=? ORDER BY m.imported_at,m.id',(workspace,workspace))
        before=conditions.get('sent_before');after=conditions.get('sent_after');group=conditions.get('group_label');event=conditions.get('event_key')
        rows=[r for r in rows if (not group or r['group_label']==group) and (not before or r['sent_at'] and r['sent_at']<=before) and (not after or r['sent_at'] and r['sent_at']>=after) and (not event or json.loads(r['metadata']).get('event_key')==event)]
        q=tokenize(query,expanded)
        if not q and not event:return []
        counts=[json.loads(r['tokens']) for r in rows]
        df=Counter(k for c in counts for k in c);n=len(rows)
        def vec(c):return {k:(1+math.log(v))*(1+math.log((n+1)/(df.get(k,0)+1))) for k,v in c.items()}
        qv=vec(q);qn=math.sqrt(sum(v*v for v in qv.values())) or 1
        scored=[]
        for row,c in zip(rows,counts):
            v=vec(c);vn=math.sqrt(sum(x*x for x in v.values())) or 1
            score=sum(x*v.get(k,0) for k,x in qv.items())/(qn*vn)
            if event or score>=.045:
                row.pop('tokens',None);row['score']=round(score,5);scored.append(row)
        return sorted(scored,key=lambda x:(-x['score'],x['id']))[:limit]
