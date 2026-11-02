import json
f1=json.load(open('data/f1.json'))
teamName={t['teamId']:t['name'] for t in f1.get('constructors',[])}
DRIVERS={d['code']:{'fam':d.get('family'),'slug':d.get('slug'),'team':d.get('teamId'),
                    'teamName':teamName.get(d.get('teamId'),''),'col':d.get('color')} for d in f1['drivers']}
LAST=f1['meta']['round']; sprintRounds=set(f1['meta'].get('sprintRounds',[]))
def drv(code): return next(x for x in f1['drivers'] if x['code']==code)
def cum(code,r): return sum((x.get('pts') or 0) for x in drv(code).get('races',[]) if x['r']<=r)
def roundPts(code,r):
    e=next((x for x in drv(code).get('races',[]) if x['r']==r),None); return (e.get('pts') or 0) if e else 0
# a driver joins the film at his first actual start (GP or sprint) -- an entry that did not start does not count
started=lambda x: x.get('status')!='Did not start' or x.get('spr') is not None
rounds=[]
for r in range(1,LAST+1):
    race=next(x for x in f1['races'] if x['r']==r)
    st=[]
    for code in DRIVERS:
        if not any(x['r']<=r and started(x) for x in drv(code).get('races',[])): continue
        st.append({'code':code,'pts':cum(code,r)})
    st.sort(key=lambda x:(-x['pts'], DRIVERS[x['code']]['fam']))
    for i,s in enumerate(st,1): s['pos']=i
    # all points scorers of the round, by finishing position
    scorers=[]
    for c in race['cls']:
        rp=roundPts(c['code'],r)
        if c['pos'].isdigit() and rp>0:
            scorers.append({'pos':int(c['pos']),'code':c['code'],'pts':rp,'teamName':DRIVERS.get(c['code'],{}).get('teamName','')})
    scorers.sort(key=lambda s:s['pos'])
    rounds.append({'round':r,'gp':race['full'],'short':race.get('short'),'flag':race.get('flag',''),
                   'circuit':race.get('circuit'),'sprint':r in sprintRounds,'standings':st,
                   'podium':scorers[:3],'scorers':scorers})
# champion: the first round after which the leader's margin beats every point still on offer
import os
SEASON=f1['meta']['season']
left=lambda r: sum(25+(8 if c['r'] in sprintRounds else 0) for c in f1['calendar'] if c['r']>r)
champion=None
for r in range(1,LAST+1):
    tbl=sorted(((cum(c,r),c) for c in DRIVERS),reverse=True)
    if tbl[0][0]-tbl[1][0]>left(r):
        d=drv(tbl[0][1]); cal=next(c for c in f1['calendar'] if c['r']==r)
        img=f'img/champion-{SEASON}.jpg'
        champion={'code':d['code'],'given':d['given'].split(' ')[-1],'family':d['family'],'season':SEASON,
                  'teamName':DRIVERS[d['code']]['teamName'],'round':r,'total':len(f1['calendar']),
                  'where':cal.get('locality') or cal['short'],'img':img if os.path.exists(img) else None}
        break
open('_film_data.js','w').write('window.FILM='+json.dumps({'totalRounds':LAST,'drivers':DRIVERS,'rounds':rounds,'champion':champion,
    'opening':[d['code'] for d in f1['drivers'] if any(x['r']==1 for x in d['races'])]}))
print('wrote _film_data.js —',LAST,'rounds')
# film.html = the template with the data inlined (self-contained page, served standalone and embedded in the app)
tpl=open('_film_standings.html').read(); tag='<script src="_film_data.js"></script>'
assert tpl.count(tag)==1
open('film.html','w').write(tpl.replace(tag,'<script>'+open('_film_data.js').read()+'</script>'))
print('wrote film.html')
