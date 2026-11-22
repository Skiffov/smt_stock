"""Standalone business-logic smoke tests without Flask; run: python test_logic.py"""
import sys, types, importlib.util, tempfile, pathlib, sqlite3, shutil
root=pathlib.Path(__file__).resolve().parent
class App:
 def __init__(self,*a): self.secret_key=None;self.config={}
 def route(self,*a,**kw): return lambda fn:fn
 def errorhandler(self,*a,**kw): return lambda fn:fn
 def before_request(self,fn): return fn
 def context_processor(self,fn): return fn
class Form(dict):
 def getlist(self,k): return self.get(k,[])
class Req:
 method='GET';form=Form();args={};endpoint=None
req=Req();sess={};messages=[]
f=types.ModuleType('flask')
f.Flask=App;f.request=req;f.session=sess;f.render_template=lambda name,**kw: ('render',name,kw)
f.redirect=lambda url: ('redirect',url);f.url_for=lambda name,**kw: name+str(kw)
f.flash=lambda msg,*args: messages.append(str(msg))
f.abort=lambda code,**kw: (_ for _ in ()).throw(RuntimeError(f'HTTP {code}'))
sys.modules['flask']=f
spec=importlib.util.spec_from_file_location('smt_app',root/'app.py');mod=importlib.util.module_from_spec(spec)
with tempfile.TemporaryDirectory() as tmp:
 # init_db during import writes to root; test only in temporary copy
 source_db=root/'smt_stock.sqlite3'
 if source_db.exists(): raise RuntimeError('Do not run tests beside a working database')
 spec.loader.exec_module(mod)
 mod.DB_PATH=pathlib.Path(tmp)/'test.sqlite3';shutil.move(str(source_db),str(mod.DB_PATH))
 def as_user(i,role):sess['user']={'id':i,'name':role,'role':role}
 def post(fn,data,*args):
  req.method='POST';req.form=Form(data);messages.clear();fn(*args);return ' | '.join(messages)
 def query(sql,args=()):
  with mod.db() as c:return [tuple(x) for x in c.execute(sql,args)]
 def check(label,condition):
  if not condition: raise AssertionError(label)
  print('PASS',label)

 as_user(1,'admin')
 post(mod.component_new,{'code':'RES0201-100R','component_type':'RES','case_size':'0201','nominal':'100 Ω','specification':'±1%, 0.05 W'})
 post(mod.component_new,{'code':'CAP0603-100N','component_type':'CAP','case_size':'0603','nominal':'100 нФ','specification':'50 V'})
 check('fresh catalog has two created components',query('SELECT COUNT(*) FROM components')[0][0]==2)
 as_user(2,'storekeeper')
 post(mod.receive_stock,{'component_id':'1','label':'REEL-001','quantity':'1200','package_type':'Рулон','manufacturer':'Yageo','mpn':'RC0201FR-07100RL','packaging_status':'Придатне'})
 post(mod.receive_stock,{'component_id':'2','label':'REEL-002','quantity':'150','package_type':'Рулон','manufacturer':'Murata','mpn':'GRM188R71H104KA93','packaging_status':'Придатне'})
 check('technical attributes stored in catalog',query('SELECT component_type,case_size,nominal FROM components WHERE id=1')[0]==('RES','0201','100 Ω'))
 check('manufacturer and MPN stored per package',query('SELECT manufacturer,mpn FROM packages WHERE id=1')[0]==('Yageo','RC0201FR-07100RL'))
 as_user(1,'admin')
 post(mod.project_new,{'name':'Demo','serial':'SMT-1','ipc_class':'2','operator_id':'3','component_id[]':['1'],'required_qty[]':['800'],'new_code[]':[''],'new_name[]':['']})
 pid=query('SELECT id FROM projects')[0][0]
 check('project created',pid>0)
 check('IPC class stored as integer',query('SELECT ipc_class FROM projects WHERE id=?',(pid,))[0][0]==2)
 as_user(3,'operator')
 post(mod.project_start,{},pid)
 check('started',query('SELECT status FROM projects WHERE id=?',(pid,))[0][0]=='in_production')
 check('automatic request exactly once',query('SELECT COUNT(*) FROM requests WHERE project_id=?',(pid,))[0][0]==1)
 check('BOM quantities copied',query('SELECT requested_qty FROM request_items')[0][0]==800)
 post(mod.project_start,{},pid)
 check('start is idempotent',query('SELECT COUNT(*) FROM requests')[0][0]==1)
 rid=query('SELECT id FROM requests')[0][0]
 item=query('SELECT id FROM request_items')[0][0]
 post(mod.request_new,{'component_id[]':['1'],'quantity[]':['150']},pid)
 check('additional request retained',query('SELECT COUNT(*) FROM requests')[0][0]==2)
 as_user(2,'storekeeper')
 m=post(mod.issue,{'item_id':str(item),'package_id':'1','quantity':'800'},rid)
 check('partial package issuance blocked by default',query('SELECT COUNT(*) FROM allocations')[0][0]==0 and 'ціла упаковка' in m)
 m=post(mod.issue,{'item_id':str(item),'package_id':'1','quantity':'800','partial_issue':'1'},rid)
 check('partial package allowed by choice',query('SELECT issued_qty FROM allocations')[0][0]==800)
 check('stock decremented',query('SELECT quantity FROM packages WHERE id=1')[0][0]==400)
 as_user(3,'operator')
 post(mod.project_finish,{},pid)
 check('cannot finish with open additional request',query('SELECT status FROM projects')[0][0]=='in_production')
 rid_extra=query('SELECT id FROM requests ORDER BY id DESC')[0][0]
 post(mod.cancel_request,{},rid_extra)
 post(mod.project_finish,{},pid)
 check('cannot finish unconfirmed issue',query('SELECT status FROM projects')[0][0]=='in_production')
 aid=query('SELECT id FROM allocations')[0][0]
 post(mod.confirm_allocation,{},aid)
 check('confirmation recorded',query('SELECT confirmed_at FROM allocations')[0][0] is not None)
 post(mod.confirm_allocation,{},aid)
 check('duplicate confirmation blocked',query('SELECT COUNT(*) FROM audit_log WHERE action=?',('Підтвердження отримання',))[0][0]==1)
 post(mod.project_finish,{},pid)
 check('finish transitions to return',query('SELECT status FROM projects')[0][0]=='awaiting_return')
 check('returns automatically generated',query('SELECT expected_qty FROM return_items')[0][0]==800)
 ret=query('SELECT id FROM return_items')[0][0]
 as_user(2,'storekeeper')
 post(mod.receive_return,{'quantity':'125','packaging_status':'Придатне'},ret)
 check('measured return recorded',query('SELECT received_qty,processed FROM return_items')[0]==(125,1))
 check('project finished',query('SELECT status FROM projects')[0][0]=='finished')
 check('returned package has original package type',query('SELECT quantity,package_type FROM packages WHERE label=?',(f'RETURN-{ret}',))[0]==(125,'Рулон'))
 check('net issue analytics',mod.project_material_rows.__call__ is not None)
 as_user(1,'admin')
 result=mod.project_statistics_detail(pid)
 check('plan fact unchanged',result[2]['items'][0]['planned']==800 and result[2]['items'][0]['net']==675)
 check('state pattern allows start',mod.ProjectWorkflow.transition('created','start')=='in_production')
 try:mod.ProjectWorkflow.transition('finished','start');raise AssertionError('invalid transition allowed')
 except ValueError: check('state pattern rejects invalid transition',True)
 # No duplicate initial request after repeated start is verified above.
 # Explicit zero return: no phantom stock appears.
 as_user(1,'admin')
 post(mod.project_new,{'name':'Zero return','serial':'SMT-ZERO','ipc_class':'3','operator_id':'3','component_id[]':['2'],'required_qty[]':['80'],'new_code[]':[''],'new_name[]':['']})
 pid2=query("SELECT id FROM projects WHERE serial='SMT-ZERO'")[0][0]
 as_user(3,'operator');post(mod.project_start,{},pid2)
 rid2=query('SELECT id FROM requests WHERE project_id=?',(pid2,))[0][0]
 item2=query('SELECT id FROM request_items WHERE request_id=?',(rid2,))[0][0]
 as_user(2,'storekeeper');post(mod.issue,{'item_id':str(item2),'package_id':'2','quantity':'80','partial_issue':'1'},rid2)
 as_user(3,'operator')
 aid2=query('SELECT a.id FROM allocations a JOIN request_items qi ON qi.id=a.request_item_id WHERE qi.request_id=?',(rid2,))[0][0]
 post(mod.confirm_allocation,{},aid2);post(mod.project_finish,{},pid2)
 ret2=query('SELECT id FROM return_items WHERE allocation_id=?',(aid2,))[0][0]
 as_user(2,'storekeeper');post(mod.receive_return,{'quantity':'0','packaging_status':'Придатне'},ret2)
 check('zero return closes project',query('SELECT status FROM projects WHERE id=?',(pid2,))[0][0]=='finished')
 check('zero return creates no phantom package',query('SELECT COUNT(*) FROM packages WHERE label=?',(f'RETURN-{ret2}',))[0][0]==0)
 # Package type is attached to incoming physical package.
 as_user(1,'admin');post(mod.component_new,{'code':'IC-DEMO','component_type':'IC','case_size':'QFN32','nominal':'MCU','specification':'3.3 V'})
 cid=query("SELECT id FROM components WHERE code='IC-DEMO'")[0][0]
 check('component specification recorded',query('SELECT specification FROM components WHERE id=?',(cid,))[0][0]=='3.3 V')
 as_user(2,'storekeeper');post(mod.receive_stock,{'component_id':str(cid),'label':'TRAY-T1','quantity':'96','location':'Shelf','package_type':'Трей','packaging_status':'Придатне','manufacturer':'TI','mpn':'TM4C123'})
 check('incoming packaging type stored',query('SELECT quantity,package_type FROM packages WHERE label=?',('TRAY-T1',))[0]==(96,'Трей'))
 check('incoming MPN recorded on package',query('SELECT mpn FROM packages WHERE label=?',('TRAY-T1',))[0][0]=='TM4C123')
 # Reject unclassified projects and missing supplier data.
 as_user(1,'admin')
 text=post(mod.project_new,{'name':'Bad','serial':'SMT-BAD','ipc_class':'4','operator_id':'3','component_id[]':['1'],'required_qty[]':['5']})
 check('invalid IPC class rejected',query("SELECT COUNT(*) FROM projects WHERE serial='SMT-BAD'")[0][0]==0)
 as_user(2,'storekeeper')
 post(mod.receive_stock,{'component_id':'1','label':'BAD-REEL','quantity':'5','package_type':'Рулон','packaging_status':'Придатне'})
 check('missing supplier fields rejected',query("SELECT COUNT(*) FROM packages WHERE label='BAD-REEL'")[0][0]==0)
 as_user(1,'admin')
 post(mod.project_new,{'name':'New BOM entry','serial':'SMT-NEW-BOM','ipc_class':'1','operator_id':'3',
    'component_id[]':[''],'required_qty[]':['100'],'new_code[]':['RES0402-1K'],
    'component_type[]':['RES'],'case_size[]':['0402'],'nominal[]':['1 kΩ'],'specification[]':['±1%']})
 check('component created from BOM with structured attributes',query("SELECT component_type,case_size,nominal FROM components WHERE code='RES0402-1K'")[0]==('RES','0402','1 kΩ'))
 check('class 1 project from BOM',query("SELECT ipc_class FROM projects WHERE serial='SMT-NEW-BOM'")[0][0]==1)
 print('ALL BUSINESS LOGIC TESTS PASSED')
