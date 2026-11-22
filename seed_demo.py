"""Create a fresh, coherent DEMO SQLite database for SMT Stock.

Run from the project directory: python seed_demo.py
Refuses to overwrite an existing database unless --reset-demo is specified;
--reset-demo only deletes a database bearing the explicit demo marker.
No production data are included. All identities and materials are fictional examples.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timedelta
import sqlite3
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB = BASE / 'smt_stock.sqlite3'
MARKER = BASE / '.smt_demo_database'


def timestamp(days: int = 0) -> str:
    return (datetime.now() + timedelta(days=days)).replace(hour=11, minute=30, second=0, microsecond=0).strftime('%Y-%m-%d %H:%M:%S')


def main():
    parser = argparse.ArgumentParser(description='Generate a test-only SMT Stock database')
    parser.add_argument('--reset-demo', action='store_true', help='replace only a previously marked demo database')
    args = parser.parse_args()
    if DB.exists():
        if not (args.reset_demo and MARKER.is_file() and MARKER.read_text().strip() == 'SMT_STOCK_DEMO_ONLY'):
            raise SystemExit('База вже існує. Її не змінено. Для скидання тільки ДЕМО-бази: python seed_demo.py --reset-demo')
        DB.unlink()
    # Read canonical CREATE TABLE declarations directly from app.py: no Flask
    # import is needed to build the demo DB, no separate schema to maintain.
    source = (BASE / 'app.py').read_text(encoding='utf-8')
    match = re.search(r"c\.executescript\('''(.*?)'''\)", source, re.S)
    if not match:
        raise SystemExit('Не вдалося знайти схему в app.py')
    con = sqlite3.connect(DB)
    con.executescript(match.group(1))
    con.executemany('INSERT INTO users(name,card_code,role) VALUES(?,?,?)',
                    [('Демо адміністратор','ADMIN001','admin'),
                     ('Демо комірник','STORE001','storekeeper'),
                     ('Демо KIT-оператор','OP001','operator')])
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        # Existing three accounts originate from app.init_db()
        con.execute("UPDATE users SET name='Олександр Демоненко' WHERE card_code='ADMIN001'")
        con.execute("UPDATE users SET name='Марія Складська' WHERE card_code='STORE001'")
        con.execute("UPDATE users SET name='Іван Китенко' WHERE card_code='OP001'")
        con.execute("INSERT INTO users(name,card_code,role) VALUES('Олена Монтажна','OP002','operator')")
        con.execute("INSERT INTO users(name,card_code,role) VALUES('Андрій Комплектувальник','STORE002','storekeeper')")
        users = {r['card_code']: r['id'] for r in con.execute('SELECT id,card_code FROM users')}
        admin, store, op, op2 = (users[k] for k in ('ADMIN001','STORE001','OP001','OP002'))

        catalog = [
            ('RES-0201-100R','Резистор 0201 100 Ω','Резистор','0201','100 Ω','±1%, 0,05 W'),
            ('RES-0603-10K','Резистор 0603 10 кΩ','Резистор','0603','10 кΩ','±1%, 0,1 W'),
            ('RES-0805-1K','Резистор 0805 1 кΩ','Резистор','0805','1 кΩ','±1%, 0,125 W'),
            ('CAP-0402-100N','Конденсатор 0402 100 нФ','Конденсатор','0402','100 нФ','X7R, 16 V'),
            ('CAP-0603-10U','Конденсатор 0603 10 мкФ','Конденсатор','0603','10 мкФ','X5R, 10 V'),
            ('CAP-0805-1U','Конденсатор 0805 1 мкФ','Конденсатор','0805','1 мкФ','X7R, 25 V'),
            ('LED-0603-GRN','Світлодіод 0603 зелений','Світлодіод','0603','Зелений','2,1 V typ.'),
            ('DIO-SOD123','Діод SOD-123 випрямний','Діод','SOD-123','1 A','60 V'),
            ('IC-SOIC8-AMP','Операційний підсилювач SOIC-8','Мікросхема','SOIC-8','Подвійний ОП','Low-noise; уточнити затверджений MPN'),
            ('CON-2P-2MM','Роз’єм 2-pin 2,0 мм','Роз’єм','THT 2P','2,0 мм','Крок 2,0 мм'),
            ('IC-QFN32-MCU','Мікроконтролер QFN-32','Мікросхема','QFN-32','MCU','Демонстраційний умовний компонент'),
            ('IND-0805-10U','Індуктивність 0805 10 мкГн','Індуктивність','0805','10 мкГн','Демонстраційна; перевіряти струм насичення'),
        ]
        components = {}
        for code,name,kind,size,nominal,spec in catalog:
            cur=con.execute('INSERT INTO components(code,name,component_type,case_size,nominal,specification,notes,created_at) VALUES(?,?,?,?,?,?,?,?)',
                            (code,name,kind,size,nominal,spec,'Демонстраційна номенклатура',timestamp(-30)))
            components[code]=cur.lastrowid

        # Packaging details demonstrate identical internal item from differing vendors.
        packages = [
            ('R-0201-001','RES-0201-100R',5000,'A-01-01','Придатне','Рулон','Yageo','RC0201FR-07100RL',''),
            ('R-0201-002','RES-0201-100R',3800,'A-01-02','Придатне','Рулон','Vishay','CRCW0201100RFKED',''),
            ('R-0603-001','RES-0603-10K',4500,'A-02-01','Придатне','Рулон','Yageo','RC0603FR-0710KL',''),
            ('R-0603-002','RES-0603-10K',2700,'A-02-02','Придатне','Рулон','Vishay','CRCW060310K0FKEA',''),
            ('R-0805-001','RES-0805-1K',2800,'A-02-03','Придатне','Стрічка','Yageo','RC0805FR-071KL',''),
            ('C-0402-001','CAP-0402-100N',6500,'B-01-01','Придатне','Рулон','Murata','GRM155R71C104KA88',''),
            ('C-0402-002','CAP-0402-100N',1600,'B-01-02','Придатне','Рулон','Samsung Electro-Mechanics','CL05B104KO5NNNC',''),
            ('C-0603-001','CAP-0603-10U',2100,'B-02-01','Придатне','Рулон','Murata','GRM188R61A106KE69',''),
            ('C-0805-001','CAP-0805-1U',3100,'B-02-02','Придатне','Рулон','TDK','C2012X7R1E105K125',''),
            ('L-0603-001','LED-0603-GRN',2200,'C-01-01','Придатне','Рулон','Lite-On','LTST-C190KGKT',''),
            ('D-001','DIO-SOD123',1200,'C-01-02','Придатне','Стрічка','onsemi','MMSD4148T1G',''),
            ('U-AMP-001','IC-SOIC8-AMP',220,'C-02-01','Придатне','Трей','Texas Instruments','LM358DR',''),
            ('J-001','CON-2P-2MM',500,'D-01-01','Придатне','Розсип','JST','B2B-PH-K-S',''),
            ('U-MCU-001','IC-QFN32-MCU',250,'D-02-01','Придатне','Трей','Demo Semiconductor','DEMO-MCU-QFN32',''),
            ('IND-001','IND-0805-10U',1200,'D-02-02','Придатне','Рулон','Demo Passives','DEMO-IND-10UH',''),
            ('R-0201-BLOCK','RES-0201-100R',450,'Q-01','Заблоковане','Рулон','Yageo','RC0201FR-07100RL','Пошкоджено несучу стрічку; частина компонентів висипалася'),
            ('C-0603-CHECK','CAP-0603-10U',300,'Q-02','Потребує перевірки','Стрічка','Murata','GRM188R61A106KE69','Невідповідність маркування на зовнішній упаковці'),
            ('U-AMP-DMG','IC-SOIC8-AMP',35,'Q-03','Пошкоджене','Туба','Texas Instruments','LM358DR','Пошкоджена туба після транспортування'),
        ]
        pkids={}
        for label,code,qty,location,status,ptype,mfg,mpn,reason in packages:
            bad=status!='Придатне'
            cur=con.execute('''INSERT INTO packages(component_id,label,quantity,location,packaging_status,package_type,manufacturer,mpn,block_reason,blocked_at,blocked_by)
              VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(components[code],label,qty,location,status,ptype,mfg,mpn,reason,timestamp(-4) if bad else None,store if bad else None))
            pkids[label]=cur.lastrowid

        project_specs=[
            ('Контролер освітлення Rev.A','SMT-DEMO-1001',1,'created',op,'Легкий демонстраційний проєкт, можна запустити під оператором.','',-2),
            ('Плата живлення 24V','SMT-DEMO-1002',2,'in_production',op,'Показати автоматичну та додаткову заявки.','',-8),
            ('Модуль індикації та керування','SMT-DEMO-1003',2,'in_production',op2,'Показати частково виконану заявку.','',-6),
            ('Промисловий модуль датчиків','SMT-DEMO-1004',3,'awaiting_return',op,'Відомість повернення для комірника.','Перевірити маркування партій перед комплектуванням.',-15),
            ('Тестова плата інтерфейсу','SMT-DEMO-1005',1,'finished',op2,'Завершений приклад для статистики план/факт.','',-23),
            ('Вузол телеметрії Rev.B','SMT-DEMO-1006',3,'finished',op,'Завершений проєкт з відхиленням план/факт.','',-28),
        ]
        projectids={}
        for name,serial,ipc,status,operator,notes,critical,days in project_specs:
            cur=con.execute('''INSERT INTO projects(name,serial,ipc_class,notes,critical_info,operator_id,status,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)''',
                    (name,serial,ipc,notes,critical,operator,status,admin,timestamp(days),timestamp(days+1)))
            projectids[serial]=cur.lastrowid
        boms={
            'SMT-DEMO-1001':[('RES-0201-100R',800),('CAP-0402-100N',800),('LED-0603-GRN',200)],
            'SMT-DEMO-1002':[('RES-0603-10K',1100),('CAP-0603-10U',500),('DIO-SOD123',160),('IND-0805-10U',180)],
            'SMT-DEMO-1003':[('RES-0201-100R',1300),('CAP-0402-100N',800),('LED-0603-GRN',260),('IC-SOIC8-AMP',100)],
            'SMT-DEMO-1004':[('RES-0805-1K',600),('CAP-0805-1U',500),('IC-SOIC8-AMP',75)],
            'SMT-DEMO-1005':[('RES-0603-10K',700),('CAP-0402-100N',500),('CON-2P-2MM',80)],
            'SMT-DEMO-1006':[('RES-0201-100R',1000),('CAP-0603-10U',700),('LED-0603-GRN',300)],
        }
        for serial,items in boms.items():
            con.executemany('INSERT INTO project_components(project_id,component_id,required_qty) VALUES(?,?,?)',
                            [(projectids[serial],components[code],qty) for code,qty in items])

        # Acknowledgements exist only where critical information was set.
        con.execute('INSERT INTO acknowledgements(project_id,user_id,acknowledged_at) VALUES(?,?,?)',
                    (projectids['SMT-DEMO-1004'],op,timestamp(-12)))
        con.execute('INSERT INTO acknowledgements(project_id,user_id,acknowledged_at) VALUES(?,?,?)',
                    (projectids['SMT-DEMO-1004'],store,timestamp(-12)))

        requestids={}
        itemids={}
        def make_request(serial, suffix, items, status, day, note):
            p=projectids[serial]
            operator=next(x[4] for x in project_specs if x[1]==serial)
            cur=con.execute('INSERT INTO requests(project_id,operator_id,status,note,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                            (p,operator,status,note,timestamp(day),timestamp(day+1)))
            rid=cur.lastrowid
            requestids[(serial,suffix)]=rid
            for code,requested,issued in items:
                row=con.execute('INSERT INTO request_items(request_id,component_id,requested_qty,issued_qty) VALUES(?,?,?,?)',
                                (rid,components[code],requested,issued))
                itemids[(serial,suffix,code)]=row.lastrowid
            return rid
        make_request('SMT-DEMO-1002','initial',[(x,q,0) for x,q in boms['SMT-DEMO-1002']], 'pending',-7,'Автоматична початкова заявка за BOM')
        make_request('SMT-DEMO-1002','extra',[('RES-0603-10K',250,0)], 'pending',-3,'Додатково для налаштування SMT-лінії')
        make_request('SMT-DEMO-1003','initial', [('RES-0201-100R',1300,1500),('CAP-0402-100N',800,0),('LED-0603-GRN',260,0),('IC-SOIC8-AMP',100,0)],'partial',-5,'Автоматична початкова заявка за BOM')
        make_request('SMT-DEMO-1004','initial',[('RES-0805-1K',600,620),('CAP-0805-1U',500,510),('IC-SOIC8-AMP',75,80)],'fulfilled',-13,'Автоматична початкова заявка за BOM')
        make_request('SMT-DEMO-1005','initial',[('RES-0603-10K',700,750),('CAP-0402-100N',500,530),('CON-2P-2MM',80,80)],'fulfilled',-21,'Автоматична початкова заявка за BOM')
        make_request('SMT-DEMO-1006','initial',[('RES-0201-100R',1000,1200),('CAP-0603-10U',700,780),('LED-0603-GRN',300,320)],'fulfilled',-26,'Автоматична початкова заявка за BOM')

        def allocate(serial,suffix,code,label,qty,returned,day,confirmation=True):
            rid=itemids[(serial,suffix,code)]
            cur=con.execute('INSERT INTO allocations(request_item_id,package_id,issued_qty,returned_qty,confirmed_at) VALUES(?,?,?,?,?)',
                        (rid,pkids[label],qty,returned,timestamp(day+1) if confirmation else None))
            return cur.lastrowid
        allocate('SMT-DEMO-1003','initial','RES-0201-100R','R-0201-001',1500,0,-4,False)
        for serial, allocations in {
            'SMT-DEMO-1004': [('RES-0805-1K','R-0805-001',620,0),('CAP-0805-1U','C-0805-001',510,0),('IC-SOIC8-AMP','U-AMP-001',80,0)],
            'SMT-DEMO-1005': [('RES-0603-10K','R-0603-001',750,65),('CAP-0402-100N','C-0402-001',530,30),('CON-2P-2MM','J-001',80,8)],
            'SMT-DEMO-1006': [('RES-0201-100R','R-0201-002',1200,100),('CAP-0603-10U','C-0603-001',780,40),('LED-0603-GRN','L-0603-001',320,12)],
        }.items():
            for code,label,qty,returned in allocations:
                aid=allocate(serial,'initial',code,label,qty,returned,-10 if serial.endswith('1004') else -18)
                if serial.endswith('1004'):
                    con.execute('INSERT INTO return_items(allocation_id,expected_qty,processed) VALUES(?,?,0)',(aid,qty))
                else:
                    ret_label='RETURN-'+str(aid)
                    original=con.execute('SELECT * FROM packages WHERE label=?',(label,)).fetchone()
                    rc=con.execute('''INSERT INTO packages(component_id,label,quantity,location,packaging_status,package_type,manufacturer,mpn)
                      VALUES(?,?,?,?,?,?,?,?)''', (original['component_id'],ret_label,returned,'Повернення','Придатне',original['package_type'],original['manufacturer'],original['mpn']))
                    con.execute('INSERT INTO return_items(allocation_id,expected_qty,received_qty,received_package_id,processed) VALUES(?,?,?,?,1)',(aid,qty,returned,rc.lastrowid))

        # Showcase meaningful audit entries rather than artificially populating history with random noise.
        actions=[
            (admin,'Створення проєкту','project',projectids['SMT-DEMO-1002'],'Плата живлення 24V',-8),
            (op,'Початок виробництва','project',projectids['SMT-DEMO-1002'],'Автоматична заявка за BOM',-7),
            (op,'Додаткова заявка','request',requestids[('SMT-DEMO-1002','extra')],'Налаштування лінії',-3),
            (store,'Видача компонентів','request',requestids[('SMT-DEMO-1003','initial')],'Рулон R-0201-001; 1500 шт.; очікує підтвердження',-4),
            (op,'Завершення роботи','project',projectids['SMT-DEMO-1004'],'Автоматична відомість повернень',-2),
            (store,'Блокування упаковки','package',pkids['R-0201-BLOCK'],'Пошкоджена несуча стрічка',-4),
            (store,'Блокування упаковки','package',pkids['C-0603-CHECK'],'Невідповідність маркування',-4),
            (store,'Приймання повернення','project',projectids['SMT-DEMO-1005'],'Облік повернених залишків',-15),
            (admin,'Створення користувача','user',users['OP002'],'Оператор іншої зміни',-20),
        ]
        con.executemany('INSERT INTO audit_log(user_id,action,entity,entity_id,details,created_at) VALUES(?,?,?,?,?,?)',
                        [(u,act,ent,eid,details,timestamp(day)) for u,act,ent,eid,details,day in actions])
        con.commit()
        issues=con.execute('PRAGMA integrity_check').fetchone()[0]
        fk=list(con.execute('PRAGMA foreign_key_check'))
        assert issues=='ok' and not fk, (issues,fk)
        assert con.execute('SELECT COUNT(*) FROM projects').fetchone()[0]==6
        assert con.execute('SELECT COUNT(*) FROM return_items WHERE processed=0').fetchone()[0]==3
        assert con.execute('SELECT COUNT(*) FROM allocations WHERE confirmed_at IS NULL').fetchone()[0]==1
        assert con.execute("SELECT COUNT(*) FROM packages WHERE packaging_status!='Придатне'").fetchone()[0]==3
        MARKER.write_text('SMT_STOCK_DEMO_ONLY\n')
        print('Готово:',DB)
        for name in ['users','components','packages','projects','project_components','requests','request_items','allocations','return_items','audit_log']:
            print(f' {name:22s} {con.execute("SELECT COUNT(*) FROM "+name).fetchone()[0]}')
        print('PRAGMA integrity_check:',issues,'; foreign keys:',len(fk))
    finally:
        con.close()

if __name__=='__main__':
    main()
