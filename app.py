from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
from functools import wraps
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager
import sqlite3, os, secrets, hmac
from project_analytics import project_material_rows, project_material_summary

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / 'smt_stock.sqlite3'
app = Flask(__name__)
app.secret_key = os.environ.get('SMT_SECRET_KEY', 'change-this-secret-before-workplace-use')
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
ROLES = {'admin':'Адміністратор', 'storekeeper':'Комірник', 'operator':'KIT-оператор'}
PROJECT_STATUSES = {'created':'Created', 'in_production':'In production', 'awaiting_return':'Awaiting return', 'finished':'Finished'}
REQUEST_STATUSES = {'pending':'Pending', 'partial':'Partially fulfilled', 'fulfilled':'Fulfilled', 'cancelled':'Cancelled'}
PACKAGING = ['Придатне', 'Пошкоджене', 'Потребує перевірки', 'Заблоковане']


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA busy_timeout=5000')
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def now(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
def me(): return session.get('user')

@app.before_request
def validate_session_and_csrf():
    if request.endpoint in ('static', 'login') or not session.get('user'):
        return
    with db() as c:
        u=c.execute('SELECT id,name,role,active FROM users WHERE id=?',(session['user']['id'],)).fetchone()
    if not u or not u['active']:
        session.clear()
        flash('Сесію завершено: обліковий запис деактивовано.','error')
        return redirect(url_for('login'))
    session['user']={'id':u['id'],'name':u['name'],'role':u['role']}
    if request.method=='POST':
        supplied=request.form.get('_csrf_token','')
        if not hmac.compare_digest(supplied,session.get('_csrf_token','')) or not supplied:
            abort(400,description='Недійсний токен форми. Оновіть сторінку.')

@app.context_processor
def csrf_context():
    if session.get('user') and not session.get('_csrf_token'):
        session['_csrf_token']=secrets.token_urlsafe(32)
    return {'csrf_token': lambda: session.get('_csrf_token',''), 'ui_theme':session.get('ui_theme','dark')}

def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not me(): return redirect(url_for('login'))
        return fn(*a, **kw)
    return w
def roles_required(*roles):
    def deco(fn):
        @wraps(fn)
        @login_required
        def w(*a, **kw):
            if me()['role'] not in roles:
                flash('Недостатньо прав для цієї дії.', 'error'); return redirect(url_for('index'))
            return fn(*a, **kw)
        return w
    return deco

class ProjectState:
    """Behavioral State pattern: each state owns its permitted transitions."""
    def next(self, event):
        raise ValueError(f'Операція {event} недоступна у стані {type(self).__name__}.')

class CreatedState(ProjectState):
    def next(self, event):
        if event=='start': return 'in_production'
        return super().next(event)

class InProductionState(ProjectState):
    def next(self, event):
        if event=='finish': return 'awaiting_return'
        return super().next(event)

class AwaitingReturnState(ProjectState):
    def next(self, event):
        if event=='close': return 'finished'
        return super().next(event)

class FinishedState(ProjectState):
    pass

class ProjectWorkflow:
    STATES = {'created': CreatedState, 'in_production': InProductionState,
              'awaiting_return': AwaitingReturnState, 'finished': FinishedState}

    @classmethod
    def transition(cls, state, event):
        if state not in cls.STATES:
            raise ValueError(f'Невідомий статус проєкту: {state}')
        return cls.STATES[state]().next(event)


PACKAGE_TYPES = ('Рулон', 'Стрічка', 'Трей', 'Туба', 'Розсип', 'Інше')

class PackageRepository:
    """Repository pattern: encapsulates package access and atomic stock decrement."""
    def __init__(self, connection):
        self.connection = connection

    def get(self, package_id):
        return self.connection.execute('SELECT * FROM packages WHERE id=?', (package_id,)).fetchone()

    def decrement_if_available(self, package_id, quantity):
        result = self.connection.execute(
            "UPDATE packages SET quantity=quantity-? WHERE id=? AND quantity>=? AND packaging_status='Придатне'",
            (quantity, package_id, quantity),
        )
        return result.rowcount == 1


def component_details(form, i=None):
    """Validated nomenclature entry, independent of manufacturer/MPN."""
    def val(field):
        if i is None: return form.get(field, '').strip()
        values=form.getlist(field+'[]')
        return values[i].strip() if i<len(values) else ''
    kind=val('component_type')
    case=val('case_size')
    nominal=val('nominal')
    spec=val('specification')
    if not kind or not case or not nominal:
        raise ValueError('Для нового компонента вкажіть тип, типорозмір та номінал.')
    return kind,case,nominal,spec, f'{kind} {case} {nominal}'

IPC_CLASSES=(1,2,3)

def audit(con, user_id, action, entity, entity_id, details=''):
    con.execute('INSERT INTO audit_log(user_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',
                (user_id, action, entity, entity_id, details, now()))

def init_db():
    """Fresh database schema. Existing databases are intentionally NOT migrated."""
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT NOT NULL, card_code TEXT UNIQUE NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','storekeeper','operator')), active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS components(id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, component_type TEXT NOT NULL, case_size TEXT NOT NULL, nominal TEXT NOT NULL, specification TEXT NOT NULL DEFAULT '', unit TEXT NOT NULL DEFAULT 'шт.', notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS packages(id INTEGER PRIMARY KEY, component_id INTEGER NOT NULL REFERENCES components(id), label TEXT UNIQUE NOT NULL, quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0), location TEXT NOT NULL DEFAULT 'Склад', packaging_status TEXT NOT NULL DEFAULT 'Придатне', package_type TEXT NOT NULL DEFAULT 'Рулон', manufacturer TEXT NOT NULL, mpn TEXT NOT NULL, block_reason TEXT NOT NULL DEFAULT '', blocked_at TEXT, blocked_by INTEGER REFERENCES users(id));
        CREATE TABLE IF NOT EXISTS projects(id INTEGER PRIMARY KEY, name TEXT NOT NULL, serial TEXT UNIQUE NOT NULL, ipc_class INTEGER NOT NULL CHECK(ipc_class IN (1,2,3)), notes TEXT DEFAULT '', critical_info TEXT DEFAULT '', operator_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'created', created_by INTEGER REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS project_components(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE, component_id INTEGER NOT NULL REFERENCES components(id), required_qty INTEGER NOT NULL CHECK(required_qty>0), UNIQUE(project_id,component_id));
        CREATE TABLE IF NOT EXISTS acknowledgements(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), user_id INTEGER NOT NULL REFERENCES users(id), acknowledged_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL REFERENCES projects(id), operator_id INTEGER NOT NULL REFERENCES users(id), status TEXT NOT NULL DEFAULT 'pending', note TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS request_items(id INTEGER PRIMARY KEY, request_id INTEGER NOT NULL REFERENCES requests(id) ON DELETE CASCADE, component_id INTEGER NOT NULL REFERENCES components(id), requested_qty INTEGER NOT NULL CHECK(requested_qty>0), issued_qty INTEGER NOT NULL DEFAULT 0 CHECK(issued_qty>=0));
        CREATE TABLE IF NOT EXISTS allocations(id INTEGER PRIMARY KEY, request_item_id INTEGER NOT NULL REFERENCES request_items(id), package_id INTEGER NOT NULL REFERENCES packages(id), issued_qty INTEGER NOT NULL CHECK(issued_qty>0), returned_qty INTEGER NOT NULL DEFAULT 0 CHECK(returned_qty>=0), confirmed_at TEXT);
        CREATE TABLE IF NOT EXISTS return_items(id INTEGER PRIMARY KEY, allocation_id INTEGER NOT NULL REFERENCES allocations(id), expected_qty INTEGER NOT NULL CHECK(expected_qty>=0), received_qty INTEGER NOT NULL DEFAULT 0 CHECK(received_qty>=0), received_package_id INTEGER REFERENCES packages(id), processed INTEGER NOT NULL DEFAULT 0, UNIQUE(allocation_id));
        CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER, details TEXT DEFAULT '', created_at TEXT NOT NULL);
        ''')
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0:
            c.executemany('INSERT INTO users(name,card_code,role) VALUES(?,?,?)', [('Демо адміністратор','ADMIN001','admin'),('Демо комірник','STORE001','storekeeper'),('Демо KIT-оператор','OP001','operator')])

def get_project(con, pid):
    p=con.execute('SELECT p.*,u.name operator_name FROM projects p JOIN users u ON u.id=p.operator_id WHERE p.id=?',(pid,)).fetchone()
    if not p: abort(404)
    if me()['role']=='operator' and p['operator_id'] != me()['id']: abort(403)
    return p

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method=='POST':
        code=request.form.get('card_code','').strip()
        with db() as c: u=c.execute('SELECT * FROM users WHERE card_code=? AND active=1',(code,)).fetchone()
        if u:
            session.clear()
            session['user']={'id':u['id'],'name':u['name'],'role':u['role']}
            session['_csrf_token']=secrets.token_urlsafe(32)
            return redirect(url_for('index'))
        flash('Картку не знайдено або обліковий запис деактивовано.','error')
    return render_template('login.html')
@app.route('/theme',methods=['POST'])
@login_required
def toggle_theme():
    session['ui_theme']='light' if session.get('ui_theme','dark')=='dark' else 'dark'
    return redirect(request.referrer if request.referrer and request.referrer.startswith(request.host_url) else url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    with db() as c:
        if me()['role']=='admin':
            projects=c.execute('SELECT p.*,u.name operator_name FROM projects p JOIN users u ON u.id=p.operator_id ORDER BY p.updated_at DESC LIMIT 12').fetchall()
            counts=c.execute('SELECT status,COUNT(*) n FROM projects GROUP BY status').fetchall()
            return render_template('index.html', projects=projects, counts=counts)
        if me()['role']=='operator':
            projects=c.execute("SELECT * FROM projects WHERE operator_id=? AND status!='finished' ORDER BY updated_at DESC",(me()['id'],)).fetchall()
            return render_template('index.html', projects=projects, counts=[])
        requests=c.execute('''SELECT r.*,p.name project_name,p.serial,u.name operator_name FROM requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.operator_id WHERE r.status IN ('pending','partial') ORDER BY r.created_at''').fetchall()
        incoming=c.execute('SELECT COUNT(*) n FROM components c WHERE NOT EXISTS (SELECT 1 FROM packages p WHERE p.component_id=c.id)').fetchone()['n']
        return render_template('index.html', requests=requests, incoming=incoming, counts=[])

@app.route('/projects')
@login_required
def projects():
    q=request.args.get('q','').strip()[:120]
    status=request.args.get('status','')
    if status not in PROJECT_STATUSES: status=''
    sort=request.args.get('sort','updated_desc')
    ordering={'updated_desc':'p.updated_at DESC,p.id DESC','updated_asc':'p.updated_at ASC,p.id ASC','name':'p.name COLLATE NOCASE ASC','serial':'p.serial COLLATE NOCASE ASC'}
    if sort not in ordering: sort='updated_desc'
    args=[f'%{q}%',f'%{q}%',f'%{q}%']
    where=['(p.name LIKE ? OR p.serial LIKE ? OR u.name LIKE ?)']
    if status: where.append('p.status=?'); args.append(status)
    if me()['role']=='operator': where.append('p.operator_id=?'); args.append(me()['id'])
    sql='SELECT p.*,u.name operator_name FROM projects p JOIN users u ON u.id=p.operator_id WHERE '+' AND '.join(where)+' ORDER BY '+ordering[sort]+' LIMIT 500'
    with db() as c: rows=c.execute(sql,args).fetchall()
    return render_template('projects.html',rows=rows,q=q,status=status,sort=sort,statuses=PROJECT_STATUSES)

@app.route('/projects/new', methods=['GET','POST'])
@roles_required('admin')
def project_new():
    if request.method=='POST':
        name=request.form.get('name','').strip(); serial=request.form.get('serial','').strip(); ipc_class=request.form.get('ipc_class','').strip()
        operator_id=request.form.get('operator_id',''); notes=request.form.get('notes','').strip(); critical=request.form.get('critical_info','').strip()
        ids=request.form.getlist('component_id[]'); qtys=request.form.getlist('required_qty[]'); new_codes=request.form.getlist('new_code[]')
        try:
            if not name or not serial or ipc_class not in ('1','2','3') or not operator_id: raise ValueError('Заповніть обов’язкові поля.')
            with db() as c:
                if not c.execute("SELECT 1 FROM users WHERE id=? AND role='operator' AND active=1",(int(operator_id),)).fetchone(): raise ValueError('Оберіть активного KIT-оператора.')
                entries=[]
                for i,q in enumerate(qtys):
                    cid=ids[i] if i<len(ids) else ''
                    code=new_codes[i].strip() if i<len(new_codes) else ''
                    if not cid and not code and not q: continue
                    n=int(q)
                    if n<=0: raise ValueError('Кількість у BOM має бути більшою за нуль.')
                    if cid:
                        if code: raise ValueError('В одному рядку виберіть наявний компонент або створіть новий, не обидва варіанти.')
                        if not c.execute('SELECT 1 FROM components WHERE id=?',(int(cid),)).fetchone(): raise ValueError('Вибраний компонент не існує.')
                        entries.append((int(cid),n))
                    else:
                        if not code: raise ValueError('Для нового компонента потрібен внутрішній артикул.')
                        kind,case,nominal,spec,name_ = component_details(request.form,i)
                        new_id=c.execute('INSERT INTO components(code,name,component_type,case_size,nominal,specification,created_at) VALUES(?,?,?,?,?,?,?)',(code,name_,kind,case,nominal,spec,now())).lastrowid
                        entries.append((new_id,n))
                if not entries: raise ValueError('Додайте хоча б один компонент до специфікації.')
                if len({x[0] for x in entries})!=len(entries): raise ValueError('Один компонент не можна додавати до BOM двічі.')
                cur=c.execute('INSERT INTO projects(name,serial,ipc_class,notes,critical_info,operator_id,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)',(name,serial,int(ipc_class),notes,critical,int(operator_id),me()['id'],now(),now()))
                pid=cur.lastrowid
                c.executemany('INSERT INTO project_components(project_id,component_id,required_qty) VALUES(?,?,?)',[(pid,*x) for x in entries])
                audit(c,me()['id'],'Створення проєкту','project',pid,f'{serial}; BOM позицій: {len(entries)}')
            flash('Проєкт створено разом зі специфікацією.','success'); return redirect(url_for('project_detail',pid=pid))
        except (ValueError,sqlite3.IntegrityError) as e: flash(str(e) or 'Перевірте дані. Серійний номер або артикул уже існує.','error')
    with db() as c:
        operators=c.execute("SELECT id,name FROM users WHERE role='operator' AND active=1 ORDER BY name").fetchall()
        components=c.execute('SELECT * FROM components ORDER BY code').fetchall()
    return render_template('project_new.html',operators=operators,components=components)

@app.route('/projects/<int:pid>/edit', methods=['GET', 'POST'])
@roles_required('admin')
def project_edit(pid):
    with db() as c:
        p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not p: abort(404)
        if p['status']!='created' or c.execute('SELECT 1 FROM requests WHERE project_id=?',(pid,)).fetchone():
            flash('Редагування доступне лише до початку виробництва та за відсутності заявок.','error')
            return redirect(url_for('project_detail',pid=pid))
        if request.method=='POST':
            name=request.form.get('name','').strip()
            serial=request.form.get('serial','').strip()
            ipc_class=request.form.get('ipc_class','').strip()
            notes=request.form.get('notes','').strip()
            critical=request.form.get('critical_info','').strip()
            operator_id=request.form.get('operator_id','')
            ids=request.form.getlist('component_id[]')
            qtys=request.form.getlist('required_qty[]')
            codes=request.form.getlist('new_code[]')
            try:
                if not name or not serial or ipc_class not in ('1','2','3') or not operator_id:
                    raise ValueError('Заповніть обов’язкові поля.')
                if not c.execute("SELECT 1 FROM users WHERE id=? AND role='operator' AND active=1",(int(operator_id),)).fetchone():
                    raise ValueError('Оберіть активного KIT-оператора.')
                entries=[]
                for i,q in enumerate(qtys):
                    cid=ids[i] if i<len(ids) else ''
                    code=codes[i].strip() if i<len(codes) else ''
                    if not cid and not code and not q: continue
                    qty=int(q)
                    if qty<=0: raise ValueError('Кількість має бути більшою за нуль.')
                    if cid:
                        if code: raise ValueError('Виберіть існуючий компонент або створіть новий.')
                        cid=int(cid)
                        if not c.execute('SELECT 1 FROM components WHERE id=?',(cid,)).fetchone():
                            raise ValueError('Компонент не знайдено.')
                    else:
                        if not code: raise ValueError('Новому компоненту потрібен артикул.')
                        kind,case,nominal,spec,name_ = component_details(request.form,i)
                        cid=c.execute('INSERT INTO components(code,name,component_type,case_size,nominal,specification,created_at) VALUES(?,?,?,?,?,?,?)',
                                      (code,name_,kind,case,nominal,spec,now())).lastrowid
                    entries.append((cid,qty))
                if not entries: raise ValueError('BOM не може бути порожнім.')
                if len(set(x[0] for x in entries))!=len(entries): raise ValueError('Дублікати компонентів у BOM.')
                c.execute('UPDATE projects SET name=?,serial=?,ipc_class=?,notes=?,critical_info=?,operator_id=?,updated_at=? WHERE id=?',
                          (name,serial,int(ipc_class),notes,critical,int(operator_id),now(),pid))
                c.execute('DELETE FROM acknowledgements WHERE project_id=?',(pid,))
                c.execute('DELETE FROM project_components WHERE project_id=?',(pid,))
                c.executemany('INSERT INTO project_components(project_id,component_id,required_qty) VALUES(?,?,?)',[(pid,*e) for e in entries])
                audit(c,me()['id'],'Редагування проєкту','project',pid,f'{serial}; BOM: {len(entries)}')
                flash('Проєкт оновлено.','success')
                return redirect(url_for('project_detail',pid=pid))
            except (ValueError,sqlite3.IntegrityError) as e:
                # Prevent partially saved components if validation fails.
                c.rollback()
                flash(f'Не вдалося зберегти: {e}','error')
        operators=c.execute("SELECT id,name FROM users WHERE role='operator' AND active=1 ORDER BY name").fetchall()
        components=c.execute('SELECT * FROM components ORDER BY code').fetchall()
        bom=c.execute('SELECT * FROM project_components WHERE project_id=? ORDER BY id',(pid,)).fetchall()
    return render_template('project_new.html',operators=operators,components=components,project=p,current_bom=bom,edit=True)

@app.route('/projects/<int:pid>/delete', methods=['POST'])
@roles_required('admin')
def project_delete(pid):
    with db() as c:
        p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not p: abort(404)
        if p['status']!='created' or c.execute('SELECT 1 FROM requests WHERE project_id=?',(pid,)).fetchone():
            flash('Видалити можна лише проєкт, виробництво якого не розпочато та для якого немає заявок.','error')
        else:
            c.execute('DELETE FROM acknowledgements WHERE project_id=?',(pid,))
            c.execute('DELETE FROM project_components WHERE project_id=?',(pid,))
            c.execute('DELETE FROM projects WHERE id=?',(pid,))
            audit(c,me()['id'],'Видалення проєкту','project',pid,f'{p["serial"]}; {p["name"]}')
            flash('Проєкт видалено.','success')
    return redirect(url_for('projects'))

@app.route('/projects/<int:pid>')
@login_required
def project_detail(pid):
    with db() as c:
        p=get_project(c,pid)
        bom=c.execute('''SELECT pc.*,co.code,co.name,co.unit,COALESCE((SELECT SUM(pk.quantity) FROM packages pk WHERE pk.component_id=co.id),0) stock FROM project_components pc JOIN components co ON co.id=pc.component_id WHERE pc.project_id=? ORDER BY co.code''',(pid,)).fetchall()
        reqs=c.execute('SELECT * FROM requests WHERE project_id=? ORDER BY created_at DESC',(pid,)).fetchall()
        ack=c.execute('SELECT * FROM acknowledgements WHERE project_id=? AND user_id=?',(pid,me()['id'])).fetchone()
        allocations=c.execute('''SELECT a.*,co.code,co.name,pk.label,rt.expected_qty FROM allocations a LEFT JOIN return_items rt ON rt.allocation_id=a.id JOIN request_items ri ON ri.id=a.request_item_id JOIN components co ON co.id=ri.component_id JOIN packages pk ON pk.id=a.package_id JOIN requests r ON r.id=ri.request_id WHERE r.project_id=? ORDER BY a.id''',(pid,)).fetchall()
        items=c.execute('''SELECT ri.*,co.code,co.name,co.unit,r.id request_id FROM request_items ri JOIN components co ON co.id=ri.component_id JOIN requests r ON r.id=ri.request_id WHERE r.project_id=? ORDER BY ri.id''',(pid,)).fetchall()
        return_form_exists=c.execute('''SELECT 1 FROM return_items ri JOIN allocations a ON a.id=ri.allocation_id JOIN request_items qi ON qi.id=a.request_item_id JOIN requests r ON r.id=qi.request_id WHERE r.project_id=?''',(pid,)).fetchone() is not None
    return render_template('project_detail.html',p=p,bom=bom,reqs=reqs,ack=ack,allocations=allocations,items=items,roles=ROLES,return_form_exists=return_form_exists)

@app.route('/projects/<int:pid>/ack',methods=['POST'])
@roles_required('operator','storekeeper')
def acknowledge(pid):
    code=request.form.get('card_code','').strip()
    with db() as c:
        p=c.execute('SELECT * FROM projects WHERE id=?',(pid,)).fetchone()
        if not p: abort(404)
        u=c.execute('SELECT id FROM users WHERE id=? AND card_code=? AND active=1',(me()['id'],code)).fetchone()
        if not u: flash('Картка не відповідає активному користувачу.','error')
        else:
            c.execute('INSERT INTO acknowledgements(project_id,user_id,acknowledged_at) VALUES(?,?,?)',(pid,me()['id'],now()))
            audit(c,me()['id'],'Підтвердження критичної інформації','project',pid,'Ознайомлення підтверджено скануванням картки')
            flash('Ознайомлення зафіксовано.','success')
    return redirect(url_for('project_detail',pid=pid))

@app.route('/projects/<int:pid>/start',methods=['POST'])
@roles_required('operator')
def project_start(pid):
    with db() as c:
        p=get_project(c,pid)
        if p['status']!='created': flash('Проєкт не перебуває у статусі Created.','error')
        elif p['critical_info'].strip() and not c.execute('SELECT 1 FROM acknowledgements WHERE project_id=? AND user_id=?',(pid,me()['id'])).fetchone(): flash('Спочатку підтвердьте ознайомлення з критичною інформацією карткою.','error')
        else:
            new_status=ProjectWorkflow.transition(p['status'], 'start')
            c.execute('UPDATE projects SET status=?,updated_at=? WHERE id=?',(new_status,now(),pid))
            bom=c.execute('SELECT component_id,required_qty FROM project_components WHERE project_id=?',(pid,)).fetchall()
            if bom:
                row=c.execute("INSERT INTO requests(project_id,operator_id,note,created_at,updated_at) VALUES(?,?,?,?,?)",(pid,me()['id'],'Автоматична початкова заявка за BOM',now(),now()))
                c.executemany('INSERT INTO request_items(request_id,component_id,requested_qty) VALUES(?,?,?)',[(row.lastrowid,x['component_id'],x['required_qty']) for x in bom])
                audit(c,me()['id'],'Автоматична заявка за BOM','request',row.lastrowid,f'Проєкт {p["serial"]}')
            audit(c,me()['id'],'Початок виробництва','project',pid)
            flash('Проєкт розпочато. Початкова заявка за BOM сформована автоматично.','success')
    return redirect(url_for('project_detail',pid=pid))

@app.route('/allocations/<int:aid>/confirm',methods=['POST'])
@roles_required('operator')
def confirm_allocation(aid):
    with db() as c:
        row=c.execute("""SELECT a.id,a.confirmed_at,a.issued_qty,r.project_id,p.operator_id,p.status
            FROM allocations a JOIN request_items ri ON ri.id=a.request_item_id
            JOIN requests r ON r.id=ri.request_id JOIN projects p ON p.id=r.project_id
            WHERE a.id=?""",(aid,)).fetchone()
        if not row: abort(404)
        if row['operator_id']!=me()['id']: abort(403)
        if row['status']!='in_production':
            flash('Підтвердження можливе лише під час виробництва.','error')
        elif row['confirmed_at']:
            flash('Отримання вже підтверджено.','error')
        else:
            c.execute('UPDATE allocations SET confirmed_at=? WHERE id=?',(now(),aid))
            audit(c,me()['id'],'Підтвердження отримання','allocation',aid,f'Кількість {row["issued_qty"]}')
            flash('Отримання упаковки підтверджено.','success')
    return redirect(url_for('project_detail',pid=row['project_id']))

@app.route('/projects/<int:pid>/finish',methods=['POST'])
@roles_required('operator')
def project_finish(pid):
    with db() as c:
        p=get_project(c,pid)
        if p['status']!='in_production':
            flash('Завершити можна лише проєкт у виробництві.','error')
        elif c.execute("SELECT 1 FROM requests WHERE project_id=? AND status IN ('pending','partial')",(pid,)).fetchone():
            flash('Спочатку виконайте або скасуйте відкриті заявки.','error')
        elif c.execute("""SELECT 1 FROM allocations a JOIN request_items ri ON ri.id=a.request_item_id
              JOIN requests r ON r.id=ri.request_id WHERE r.project_id=? AND a.confirmed_at IS NULL""",(pid,)).fetchone():
            flash('Спочатку підтвердьте отримання кожної виданої упаковки.','error')
        else:
            new_status=ProjectWorkflow.transition(p['status'],'finish')
            c.execute('UPDATE projects SET status=?,updated_at=? WHERE id=?',(new_status,now(),pid))
            rows=c.execute("""SELECT a.id,a.issued_qty,a.returned_qty FROM allocations a
                JOIN request_items ri ON ri.id=a.request_item_id JOIN requests r ON r.id=ri.request_id
                WHERE r.project_id=?""",(pid,)).fetchall()
            # Storekeeper will enter measured physical return, including an explicit zero.
            for row in rows:
                remaining=row['issued_qty']-row['returned_qty']
                if remaining>0:
                    c.execute('INSERT OR IGNORE INTO return_items(allocation_id,expected_qty,processed) VALUES(?,?,0)',(row['id'],remaining))
            audit(c,me()['id'],'Завершення роботи та автоматична відомість повернень','project',pid)
            pending=c.execute("""SELECT 1 FROM return_items rt JOIN allocations a ON a.id=rt.allocation_id
              JOIN request_items ri ON ri.id=a.request_item_id JOIN requests r ON r.id=ri.request_id
              WHERE r.project_id=? AND rt.processed=0""",(pid,)).fetchone()
            if not pending:
                c.execute('UPDATE projects SET status=?,updated_at=? WHERE id=?',(ProjectWorkflow.transition(new_status,'close'),now(),pid))
                flash('Проєкт завершено: немає матеріалів для повернення.','success')
            else:
                flash('Відомість повернення створена. Комірник визначить фактичні залишки.','success')
    return redirect(url_for('project_detail',pid=pid))

@app.route('/projects/<int:pid>/requests/new',methods=['POST'])
@roles_required('operator')
def request_new(pid):
    ids=request.form.getlist('component_id[]'); qtys=request.form.getlist('quantity[]'); note=request.form.get('note','').strip()
    try:
        with db() as c:
            p=get_project(c,pid)
            if p['status']!='in_production': raise ValueError('Заявки можна створювати лише під час In production.')
            rows=[]
            for cid,q in zip(ids,qtys):
                if not cid and not q: continue
                if not cid or not q: raise ValueError('Заповніть компонент і кількість або видаліть порожній рядок.')
                cid=int(cid); q=int(q)
                if q<=0: raise ValueError('Кількість має бути більшою за нуль.')
                if not c.execute('SELECT 1 FROM project_components WHERE project_id=? AND component_id=?',(pid,cid)).fetchone(): raise ValueError('Компонент відсутній у специфікації проєкту.')
                rows.append((cid,q))
            if not rows: raise ValueError('Додайте хоча б одну позицію.')
            if len({cid for cid,_ in rows})!=len(rows): raise ValueError('Компонент повторюється в заявці. Об’єднайте кількість в один рядок.')
            cur=c.execute('INSERT INTO requests(project_id,operator_id,note,created_at,updated_at) VALUES(?,?,?,?,?)',(pid,me()['id'],note,now(),now()))
            rid=cur.lastrowid; c.executemany('INSERT INTO request_items(request_id,component_id,requested_qty) VALUES(?,?,?)',[(rid,*x) for x in rows])
            audit(c,me()['id'],'Створення заявки','request',rid,f'Проєкт {p["serial"]}')
        flash('Заявку передано комірнику.','success')
    except (ValueError,sqlite3.IntegrityError) as e: flash(str(e),'error')
    return redirect(url_for('project_detail',pid=pid))

@app.route('/projects/<int:pid>/returns',methods=['POST'])
@roles_required('operator')
def submit_returns(pid):
    flash('Повернення формуються автоматично. Кількість визначає комірник.','error')
    return redirect(url_for('project_detail',pid=pid))

@app.route('/requests')
@roles_required('storekeeper','admin')
def requests_page():
    q=request.args.get('q','').strip()[:120]
    status=request.args.get('status','')
    if status not in REQUEST_STATUSES: status=''
    sort=request.args.get('sort','priority')
    ordering={'priority':"CASE r.status WHEN 'pending' THEN 0 WHEN 'partial' THEN 1 ELSE 2 END,r.created_at",'newest':'r.created_at DESC,r.id DESC','oldest':'r.created_at ASC,r.id ASC','project':'p.name COLLATE NOCASE ASC'}
    if sort not in ordering: sort='priority'
    args=[f'%{q}%',f'%{q}%',f'%{q}%']
    where=['(p.name LIKE ? OR p.serial LIKE ? OR u.name LIKE ?)']
    if status: where.append('r.status=?'); args.append(status)
    sql='SELECT r.*,p.name project_name,p.serial,u.name operator_name FROM requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.operator_id WHERE '+' AND '.join(where)+' ORDER BY '+ordering[sort]+' LIMIT 500'
    with db() as c: rows=c.execute(sql,args).fetchall()
    return render_template('requests.html',rows=rows,q=q,status=status,sort=sort,statuses=REQUEST_STATUSES)

@app.route('/requests/<int:rid>')
@roles_required('storekeeper','admin')
def request_detail(rid):
    with db() as c:
        r=c.execute('''SELECT r.*,p.name project_name,p.serial,p.status project_status,u.name operator_name FROM requests r JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=r.operator_id WHERE r.id=?''',(rid,)).fetchone()
        if not r: abort(404)
        project=c.execute('SELECT * FROM projects WHERE id=?',(r['project_id'],)).fetchone()
        ack=c.execute('SELECT * FROM acknowledgements WHERE project_id=? AND user_id=?',(r['project_id'],me()['id'])).fetchone()
        items=c.execute('''SELECT ri.*,co.code,co.name,co.unit FROM request_items ri JOIN components co ON co.id=ri.component_id WHERE ri.request_id=?''',(rid,)).fetchall()
        packages=c.execute('''SELECT pk.*,co.code,co.name,co.unit FROM packages pk JOIN components co ON co.id=pk.component_id ORDER BY co.code,pk.label''').fetchall()
    return render_template('request_detail.html',r=r,items=items,packages=packages,project=project,ack=ack)

@app.route('/requests/<int:rid>/issue',methods=['POST'])
@roles_required('storekeeper','admin')
def issue(rid):
    try:
        item_id=int(request.form['item_id']); package_id=int(request.form['package_id']); qty=int(request.form['quantity'])
        if qty<=0: raise ValueError()
        with db() as c:
            ri=c.execute('SELECT ri.*,r.id request_id,r.project_id,r.status FROM request_items ri JOIN requests r ON r.id=ri.request_id WHERE ri.id=? AND r.id=?',(item_id,rid)).fetchone()
            package_repository = PackageRepository(c)
            pk=package_repository.get(package_id)
            if not ri or not pk or pk['component_id']!=ri['component_id']: raise ValueError()
            project=c.execute('SELECT status,critical_info FROM projects WHERE id=?',(ri['project_id'],)).fetchone()
            if project['status']!='in_production': raise ValueError('Видача дозволена лише для проєкту In production.')
            ack=c.execute('SELECT 1 FROM acknowledgements WHERE project_id=? AND user_id=?',(ri['project_id'],me()['id'])).fetchone()
            if project['critical_info'] and not ack: raise ValueError('Спочатку відкрийте проєкт і підтвердьте критичну інформацію своєю карткою.')
            if ri['status']=='cancelled': raise ValueError('Цю заявку скасовано.')
            if pk['packaging_status']!='Придатне': raise ValueError('Видача з непридатної або заблокованої упаковки заборонена.')
            if pk['quantity']<qty: raise ValueError('Недостатній залишок у вибраній упаковці.')
            if qty!=pk['quantity'] and request.form.get('partial_issue')!='1':
                raise ValueError('За замовчуванням видається ціла упаковка. Для часткової видачі встановіть відповідну позначку.')
            if not package_repository.decrement_if_available(package_id,qty): raise ValueError('Недостатній залишок або упаковку заблоковано.')
            c.execute('UPDATE request_items SET issued_qty=issued_qty+? WHERE id=?',(qty,item_id))
            c.execute('INSERT INTO allocations(request_item_id,package_id,issued_qty) VALUES(?,?,?)',(item_id,package_id,qty))
            audit(c,me()['id'],'Видача компонентів','request',rid,f'{pk["label"]} ({pk["package_type"]}): передано {qty}; очікує підтвердження оператора')
            totals=c.execute('SELECT SUM(requested_qty) req,SUM(issued_qty) iss FROM request_items WHERE request_id=?',(rid,)).fetchone()
            status='fulfilled' if c.execute('SELECT 1 FROM request_items WHERE request_id=? AND issued_qty<requested_qty',(rid,)).fetchone() is None else ('partial' if totals['iss']>0 else 'pending')
            c.execute('UPDATE requests SET status=?,updated_at=? WHERE id=?',(status,now(),rid))
        flash('Видачу зареєстровано.','success')
    except (ValueError,KeyError,sqlite3.IntegrityError) as e: flash(str(e) if str(e) else 'Не вдалося виконати видачу.','error')
    return redirect(url_for('request_detail',rid=rid))

@app.route('/requests/<int:rid>/cancel',methods=['POST'])
@roles_required('operator','admin')
def cancel_request(rid):
    with db() as c:
        r=c.execute('SELECT r.*,p.operator_id,p.status project_status FROM requests r JOIN projects p ON p.id=r.project_id WHERE r.id=?',(rid,)).fetchone()
        if not r: abort(404)
        if me()['role']=='operator' and r['operator_id']!=me()['id']: abort(403)
        if r['status'] not in ('pending','partial') or r['project_status']!='in_production':
            flash('Цю заявку вже не можна скасувати.','error')
        else:
            c.execute("UPDATE requests SET status='cancelled',updated_at=? WHERE id=?",(now(),rid))
            audit(c,me()['id'],'Скасування заявки','request',rid)
            flash('Невиконану частину заявки скасовано. Уже видані компоненти залишаються на обліку проєкту.','success')
    return redirect(url_for('project_detail',pid=r['project_id']))

@app.route('/projects/<int:pid>/returns/revise',methods=['POST'])
@roles_required('operator')
def revise_returns(pid):
    flash('Відомість повернення контролює комірник.','error')
    return redirect(url_for('project_detail',pid=pid))

@app.route('/returns')
@roles_required('storekeeper')
def returns_page():
    with db() as c:
        rows=c.execute("""SELECT ri.id return_id,ri.expected_qty,ri.received_qty,(ri.expected_qty-ri.received_qty) remaining,a.id allocation_id,pk.label,co.code,co.name,co.unit,p.id project_id,p.name project_name,p.serial,u.name operator_name FROM return_items ri JOIN allocations a ON a.id=ri.allocation_id JOIN request_items qi ON qi.id=a.request_item_id JOIN requests r ON r.id=qi.request_id JOIN projects p ON p.id=r.project_id JOIN users u ON u.id=p.operator_id JOIN packages pk ON pk.id=a.package_id JOIN components co ON co.id=qi.component_id WHERE ri.processed=0 AND p.status='awaiting_return' ORDER BY p.updated_at""").fetchall()
    return render_template('returns.html',rows=rows)

@app.route('/returns/<int:rid>',methods=['POST'])
@roles_required('storekeeper')
def receive_return(rid):
    try:
        qty=int(request.form['quantity']); status=request.form.get('packaging_status','Придатне')
        if qty<0 or status not in PACKAGING: raise ValueError('Перевірте кількість і стан пакування.')
        reason=request.form.get('block_reason','').strip()[:1000]
        if qty>0 and status!='Придатне' and not reason: raise ValueError('Для непридатної упаковки потрібна причина.')
        with db() as c:
            item=c.execute("""SELECT ri.*,a.id allocation_id,a.package_id,a.issued_qty,a.returned_qty,pk.label,r.project_id FROM return_items ri JOIN allocations a ON a.id=ri.allocation_id JOIN packages pk ON pk.id=a.package_id JOIN request_items qi ON qi.id=a.request_item_id JOIN requests r ON r.id=qi.request_id WHERE ri.id=?""",(rid,)).fetchone()
            if not item or item['processed'] or item['received_qty']+qty>item['expected_qty']: raise ValueError('Кількість більша за видану або відомість вже оброблена.')
            if c.execute('SELECT status FROM projects WHERE id=?',(item['project_id'],)).fetchone()['status']!='awaiting_return':
                raise ValueError('Проєкт не очікує повернень.')
            c.execute('UPDATE return_items SET received_qty=received_qty+?,processed=1 WHERE id=?',(qty,rid))
            c.execute('UPDATE allocations SET returned_qty=returned_qty+? WHERE id=?',(qty,item['allocation_id']))
            # Returned material is accounted for separately; the original reel remains unchanged.
            old=c.execute('SELECT * FROM packages WHERE id=?',(item['package_id'],)).fetchone()
            return_label=f'RETURN-{rid}'
            ret=c.execute('SELECT * FROM packages WHERE label=?',(return_label,)).fetchone()
            if qty==0:
                ret_id=None
            elif ret:
                if ret['packaging_status']!=status:
                    raise ValueError('Для додаткового приймання використайте той самий стан пакування, що й раніше.')
                c.execute('UPDATE packages SET quantity=quantity+? WHERE id=?',(qty,ret['id']))
                ret_id=ret['id']
            else:
                ret_id=c.execute('INSERT INTO packages(component_id,label,quantity,location,packaging_status,package_type,manufacturer,mpn,block_reason,blocked_at,blocked_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (old['component_id'],return_label,qty,'Повернення / перевірка',status,old['package_type'],old['manufacturer'],old['mpn'],reason if status!='Придатне' else '',now() if status!='Придатне' else None,me()['id'] if status!='Придатне' else None)).lastrowid
            c.execute('UPDATE return_items SET received_package_id=? WHERE id=?',(ret_id,rid))
            audit(c,me()['id'],'Приймання повернення','project',item['project_id'],f'{item["label"]}: {qty}; пакування: {status}')
            pending=c.execute("""SELECT COUNT(*) n FROM return_items ri JOIN allocations a ON a.id=ri.allocation_id JOIN request_items qi ON qi.id=a.request_item_id JOIN requests r ON r.id=qi.request_id WHERE r.project_id=? AND ri.processed=0""",(item['project_id'],)).fetchone()['n']
            if pending==0:
                c.execute('UPDATE projects SET status=?,updated_at=? WHERE id=?',
                    (ProjectWorkflow.transition('awaiting_return','close'),now(),item['project_id']))
                audit(c,me()['id'],'Проєкт закрито','project',item['project_id'],'Усі заявлені повернення прийнято')
        flash('Повернення прийнято.','success')
    except (ValueError,KeyError) as e: flash(str(e),'error')
    return redirect(url_for('returns_page'))

@app.route('/components')
@login_required
def components():
    q=request.args.get('q','').strip()[:120]
    stock=request.args.get('stock','all')
    if stock not in ('all','available','zero','quarantine'): stock='all'
    sort=request.args.get('sort','code')
    ordering={'code':'co.code COLLATE NOCASE ASC','name':'co.name COLLATE NOCASE ASC','stock_desc':'stock DESC,co.code','stock_asc':'stock ASC,co.code'}
    if sort not in ordering: sort='code'
    having={'all':'1=1','available':'usable_stock>0','zero':'usable_stock=0','quarantine':'blocked_packages>0'}[stock]
    sql=f"""SELECT co.*,COALESCE(SUM(pk.quantity),0) stock,
        COALESCE(SUM(CASE WHEN pk.packaging_status='Придатне' THEN pk.quantity ELSE 0 END),0) usable_stock,
        COALESCE(SUM(CASE WHEN pk.packaging_status!='Придатне' THEN pk.quantity ELSE 0 END),0) quarantined_stock,
        SUM(CASE WHEN pk.packaging_status!='Придатне' THEN 1 ELSE 0 END) blocked_packages,
        COUNT(pk.id) packages
        FROM components co LEFT JOIN packages pk ON pk.component_id=co.id
        WHERE (co.code LIKE ? OR co.name LIKE ? OR EXISTS (SELECT 1 FROM packages pp WHERE pp.component_id=co.id AND (pp.mpn LIKE ? OR pp.manufacturer LIKE ?)))
        GROUP BY co.id HAVING {having} ORDER BY {ordering[sort]} LIMIT 500"""
    with db() as c: rows=c.execute(sql,(f'%{q}%',)*4).fetchall()
    return render_template('components.html',rows=rows,q=q,stock=stock,sort=sort)

@app.route('/components/<int:cid>/packages')
@login_required
def component_packages(cid):
    with db() as c:
        comp=c.execute('SELECT * FROM components WHERE id=?',(cid,)).fetchone()
        if not comp: abort(404)
        packages=c.execute('SELECT pk.*,u.name blocked_by_name FROM packages pk LEFT JOIN users u ON u.id=pk.blocked_by WHERE pk.component_id=? ORDER BY pk.label COLLATE NOCASE',(cid,)).fetchall()
    return render_template('component_packages.html',comp=comp,packages=packages)

@app.route('/components/new',methods=['GET','POST'])
@roles_required('admin','storekeeper')
def component_new():
    if request.method=='POST':
        try:
            code=request.form.get('code','').strip()
            if not code: raise ValueError('Вкажіть внутрішній артикул.')
            kind,case,nominal,spec,name=component_details(request.form)
            with db() as c:
                cur=c.execute('INSERT INTO components(code,name,component_type,case_size,nominal,specification,notes,created_at) VALUES(?,?,?,?,?,?,?,?)',
                    (code,name,kind,case,nominal,spec,request.form.get('notes','').strip(),now()))
                audit(c,me()['id'],'Створення компонента','component',cur.lastrowid,'Початковий залишок: 0')
            flash('Компонент створено з нульовим залишком.','success'); return redirect(url_for('components'))
        except (ValueError,sqlite3.IntegrityError) as e: flash(str(e) or 'Такий артикул уже існує.','error')
    return render_template('component_new.html')

@app.route('/packages/receive',methods=['GET','POST'])
@roles_required('storekeeper')
def receive_stock():
    with db() as c: comps=c.execute('SELECT * FROM components ORDER BY code').fetchall()
    if request.method=='POST':
        try:
            cid=int(request.form['component_id']); qty=int(request.form['quantity']); label=request.form['label'].strip(); loc=request.form.get('location','Склад').strip() or 'Склад'
            if qty<=0 or not label or request.form.get('packaging_status','Придатне') not in PACKAGING: raise ValueError()
            status=request.form.get('packaging_status','Придатне'); reason=request.form.get('block_reason','').strip()[:1000]
            manufacturer=request.form.get('manufacturer','').strip(); mpn=request.form.get('mpn','').strip()
            if not manufacturer or not mpn: raise ValueError('Для упаковки потрібні виробник і MPN.')
            package_type=request.form.get('package_type','Рулон')
            if package_type not in PACKAGE_TYPES: raise ValueError('Некоректний тип пакування.')
            if status!='Придатне' and not reason: raise ValueError('Потрібна причина блокування.')
            with db() as c:
                cur=c.execute('INSERT INTO packages(component_id,label,quantity,location,packaging_status,package_type,manufacturer,mpn,block_reason,blocked_at,blocked_by) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(cid,label,qty,loc,status,package_type,manufacturer,mpn,reason if status!='Придатне' else '',now() if status!='Придатне' else None,me()['id'] if status!='Придатне' else None))
                audit(c,me()['id'],'Надходження компонента','package',cur.lastrowid,f'{label}; пакування {package_type}; кількість {qty}')
            flash('Надходження оприбутковано.','success'); return redirect(url_for('components'))
        except (ValueError,KeyError,sqlite3.IntegrityError) as e: flash(str(e) or 'Перевірте компонент, кількість і унікальність мітки упаковки.','error')
    return render_template('receive.html',comps=comps,packaging=PACKAGING,package_types=PACKAGE_TYPES)

@app.route('/block-zone')
@roles_required('storekeeper','admin')
def block_zone():
    q=request.args.get('q','').strip()[:120]
    status=request.args.get('status','')
    if status not in PACKAGING or status=='Придатне': status=''
    ordering={'newest':'pk.id DESC','label':'pk.label COLLATE NOCASE ASC','quantity':'pk.quantity DESC,pk.id DESC'}
    sort=request.args.get('sort','newest')
    if sort not in ordering: sort='newest'
    where=["pk.packaging_status!='Придатне'",'(pk.label LIKE ? OR co.code LIKE ? OR co.name LIKE ? OR pk.block_reason LIKE ?)']
    args=[f'%{q}%']*4
    if status: where.append('pk.packaging_status=?');args.append(status)
    sql="SELECT pk.*,co.code,co.name,co.unit,u.name blocked_by_name FROM packages pk JOIN components co ON co.id=pk.component_id LEFT JOIN users u ON u.id=pk.blocked_by WHERE "+' AND '.join(where)+' ORDER BY '+ordering[sort]+' LIMIT 500'
    with db() as c:
        rows=c.execute(sql,args).fetchall()
        summary=c.execute("SELECT COUNT(*) packages,COALESCE(SUM(quantity),0) pieces FROM packages WHERE packaging_status!='Придатне'").fetchone()
        ready=c.execute("SELECT pk.*,co.code,co.name,co.unit FROM packages pk JOIN components co ON co.id=pk.component_id WHERE pk.packaging_status='Придатне' AND pk.quantity>0 AND (pk.label LIKE ? OR co.code LIKE ? OR co.name LIKE ?) ORDER BY pk.label LIMIT 150",(f'%{q}%',)*3).fetchall()
    return render_template('block_zone.html',rows=rows,ready=ready,summary=summary,q=q,status=status,sort=sort,packaging=PACKAGING)

@app.route('/packages/<int:package_id>/status',methods=['POST'])
@roles_required('storekeeper','admin')
def change_package_status(package_id):
    status=request.form.get('packaging_status','')
    reason=request.form.get('block_reason','').strip()[:1000]
    if status not in PACKAGING or (status!='Придатне' and not reason):
        flash('Для блокування або обмеження упаковки вкажіть стан та причину.','error')
        return redirect(url_for('block_zone'))
    with db() as c:
        pk=c.execute('SELECT * FROM packages WHERE id=?',(package_id,)).fetchone()
        if not pk: abort(404)
        if status=='Придатне':
            c.execute("UPDATE packages SET packaging_status=?,block_reason='',blocked_at=NULL,blocked_by=NULL WHERE id=?",(status,package_id))
            details=f'{pk["label"]}: розблоковано; попередня причина: {pk["block_reason"] or "не вказана"}; коментар: {reason or "—"}'
        else:
            c.execute('UPDATE packages SET packaging_status=?,block_reason=?,blocked_at=?,blocked_by=? WHERE id=?',(status,reason,now(),me()['id'],package_id))
            details=f'{pk["label"]}: {status}; причина: {reason}'
        audit(c,me()['id'],'Зміна стану упаковки','package',package_id,details)
    flash('Стан упаковки збережено.','success')
    return redirect(url_for('block_zone'))

@app.route('/statistics')
@roles_required('admin','storekeeper')
def statistics():
    start=request.args.get('from','').strip()
    end=request.args.get('to','').strip()
    try:
        if start: datetime.strptime(start,'%Y-%m-%d')
        if end: datetime.strptime(end,'%Y-%m-%d')
        if start and end and start>end: raise ValueError()
    except ValueError:
        flash('Некоректний діапазон дат.','error'); return redirect(url_for('statistics'))
    conditions=[];args=[]
    if start: conditions.append('date(req.created_at)>=?');args.append(start)
    if end: conditions.append('date(req.created_at)<=?');args.append(end)
    where=' AND '.join(conditions) if conditions else '1=1'
    with db() as c:
        rows=c.execute(f"""SELECT co.id,co.code,co.name,co.unit,
            COALESCE(SUM(a.issued_qty),0) issued,
            COALESCE(SUM(a.returned_qty),0) returned,
            COALESCE(SUM(a.issued_qty-a.returned_qty),0) net,
            COUNT(DISTINCT req.project_id) projects,
            COUNT(a.id) operations
          FROM allocations a
          JOIN request_items ri ON ri.id=a.request_item_id
          JOIN requests req ON req.id=ri.request_id
          JOIN components co ON co.id=ri.component_id
          WHERE {where}
          GROUP BY co.id ORDER BY net DESC,co.code LIMIT 100""",args).fetchall()
        totals=c.execute(f"""SELECT COUNT(DISTINCT req.project_id) projects,COUNT(a.id) issues,
            COALESCE(SUM(a.issued_qty),0) issued,COALESCE(SUM(a.returned_qty),0) returned
            FROM allocations a JOIN request_items ri ON ri.id=a.request_item_id
            JOIN requests req ON req.id=ri.request_id WHERE {where}""",args).fetchone()
        blocked=c.execute("SELECT COUNT(*) n FROM packages WHERE packaging_status!='Придатне'").fetchone()['n']
    return render_template('statistics.html',rows=rows,totals=totals,blocked=blocked,start=start,end=end)

@app.route('/statistics/projects')
@roles_required('admin', 'storekeeper')
def project_statistics():
    search = request.args.get('q', '').strip()[:100]
    status = request.args.get('status', '').strip()
    if status not in PROJECT_STATUSES:
        status = ''
    filters = []
    params = []
    if search:
        filters.append('(p.name LIKE ? OR p.serial LIKE ?)')
        params.extend((f'%{search}%', f'%{search}%'))
    if status:
        filters.append('p.status=?')
        params.append(status)
    where = ('WHERE ' + ' AND '.join(filters)) if filters else ''
    with db() as c:
        projects = c.execute(f'''SELECT p.id,p.name,p.serial,p.status,p.created_at,
            COUNT(pc.id) AS bom_lines
            FROM projects p LEFT JOIN project_components pc ON pc.project_id=p.id
            {where} GROUP BY p.id ORDER BY p.created_at DESC,p.id DESC LIMIT 200''', params).fetchall()
        cards = []
        for project in projects:
            row = dict(project)
            detail = project_material_rows(c, row['id'])
            row.update(project_material_summary(detail))
            cards.append(row)
    return render_template('project_statistics.html', projects=cards, search=search,
                           selected_status=status, statuses=PROJECT_STATUSES)


@app.route('/statistics/projects/<int:pid>')
@roles_required('admin', 'storekeeper')
def project_statistics_detail(pid):
    with db() as c:
        project = c.execute('''SELECT p.*, u.name operator_name FROM projects p
                              JOIN users u ON u.id=p.operator_id WHERE p.id=?''', (pid,)).fetchone()
        if not project:
            abort(404)
        items = project_material_rows(c, pid)
        summary = project_material_summary(items)
        pending_returns = c.execute('''SELECT COUNT(*) AS n FROM return_items rt
            JOIN allocations a ON a.id=rt.allocation_id
            JOIN request_items ri ON ri.id=a.request_item_id
            JOIN requests r ON r.id=ri.request_id
            WHERE r.project_id=? AND rt.processed=0''', (pid,)).fetchone()['n']
    return render_template('project_statistics_detail.html', project=project,
                           items=items, summary=summary, pending_returns=pending_returns,
                           statuses=PROJECT_STATUSES)


@app.route('/users',methods=['GET','POST'])
@roles_required('admin')
def users():
    if request.method=='POST':
        try:
            name=request.form.get('name','').strip(); code=request.form.get('card_code','').strip(); role=request.form.get('role','')
            if not name or not code or role not in ROLES: raise ValueError()
            with db() as c: c.execute('INSERT INTO users(name,card_code,role) VALUES(?,?,?)',(name,code,role)); audit(c,me()['id'],'Створення користувача','user',None,f'{name}; {role}')
            flash('Користувача створено.','success')
        except (ValueError,sqlite3.IntegrityError): flash('Перевірте дані. Код картки має бути унікальним.','error')
    with db() as c: rows=c.execute('SELECT * FROM users ORDER BY role,name').fetchall()
    return render_template('users.html',rows=rows,roles=ROLES)

@app.route('/users/<int:uid>/toggle',methods=['POST'])
@roles_required('admin')
def toggle_user(uid):
    if uid==me()['id']: flash('Не можна деактивувати власний обліковий запис.','error')
    else:
        with db() as c:
            c.execute('UPDATE users SET active=1-active WHERE id=?',(uid,)); audit(c,me()['id'],'Зміна активності користувача','user',uid)
        flash('Статус користувача змінено.','success')
    return redirect(url_for('users'))

@app.route('/history')
@roles_required('admin','storekeeper')
def history():
    with db() as c: rows=c.execute('SELECT a.*,u.name user_name FROM audit_log a LEFT JOIN users u ON u.id=a.user_id ORDER BY a.id DESC LIMIT 300').fetchall()
    return render_template('history.html',rows=rows)

@app.errorhandler(403)
def forbidden(e): return render_template('error.html',message='Доступ заборонено для цієї ролі.'),403
@app.errorhandler(404)
def missing(e): return render_template('error.html',message='Запис не знайдено.'),404

init_db()
if __name__=='__main__':
    app.run(host='127.0.0.1',port=5000,debug=os.environ.get('SMT_DEBUG')=='1')
