import json
f1=json.load(open('data/f1.json'))
teamName={t['teamId']:t['name'] for t in f1.get('constructors',[])}
DRIVERS={d['code']:{'fam':d.get('family'),'slug':d.get('slug'),'team':d.get('teamId'),
                    'teamName':teamName.get(d.get('teamId'),''),'col':d.get('color')} for d in f1['drivers']}
LAST=f1['meta']['round']; sprintRounds=set(f1.get('sprintRounds',[]))
def drv(code): return next(x for x in f1['drivers'] if x['code']==code)
def cum(code,r): return sum((x.get('pts') or 0) for x in drv(code).get('races',[]) if x['r']<=r)
def roundPts(code,r):
    e=next((x for x in drv(code).get('races',[]) if x['r']==r),None); return (e.get('pts') or 0) if e else 0
rounds=[]
for r in range(1,LAST+1):
    race=next(x for x in f1['races'] if x['r']==r)
    st=[]
    for code in DRIVERS:
        if not any(x['r']<=r for x in drv(code).get('races',[])): continue
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
open('_film_data.js','w').write('window.FILM='+json.dumps({'totalRounds':LAST,'drivers':DRIVERS,'rounds':rounds}))
print('wrote _film_data.js —',LAST,'rounds')
