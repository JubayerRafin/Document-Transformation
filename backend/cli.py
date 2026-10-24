import argparse, getpass, json, os
from sqlalchemy import select
from .config import Settings
from .db import database, User
from .security import password_hash

def main():
    p=argparse.ArgumentParser(description='Local administration. Never submit credentials in chat.')
    sub=p.add_subparsers(dest='action',required=True)
    u=sub.add_parser('user');u.add_argument('name');u.add_argument('--role',choices=['admin','reviewer','viewer','customer'],default='customer')
    b=sub.add_parser('import-baseline');b.add_argument('root');b.add_argument('--owner',required=True)
    sub.add_parser('status')
    args=p.parse_args();cfg=Settings.load();engine,sessions=database(cfg)
    if args.action=='user':
        password=getpass.getpass('Password (12+ characters): ')
        if password!=getpass.getpass('Confirm password: '):raise SystemExit('Passwords differ.')
        with sessions.begin() as s:
            existing=s.scalar(select(User).where(User.name==args.name))
            if existing:existing.password=password_hash(password);existing.role=args.role
            else:s.add(User(name=args.name,password=password_hash(password),role=args.role))
        print('Account configured:',args.name,args.role)
    elif args.action=='import-baseline':
        from .baseline import import_baseline
        print('Saved documents registered:',len(import_baseline(sessions,cfg,args.root,args.owner)))
    else:
        with sessions() as s:count=s.query(User).count()
        print(json.dumps({'database':engine.dialect.name,'users':count,'worker_enabled':bool(cfg.worker_profile.get('enabled'))}))
    engine.dispose()
if __name__=='__main__':main()
