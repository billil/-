"""Trusted operator only; do not expose this module as a public endpoint."""
import argparse
import sqlite3
import time
from pathlib import Path
from group_access import GroupAccess


def provision(account_db, message_db, bot_workspace, group_key, alias, name, admin_username):
    vals=(bot_workspace,group_key,alias,name,admin_username)
    if any(not isinstance(v,str) or not v.strip() or len(v)>160 for v in vals):
        raise ValueError('invalid provisioning input')
    read=sqlite3.connect(Path(message_db).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        item=read.execute('SELECT source FROM groups WHERE workspace=? AND group_key=? AND authorized=1',(bot_workspace,group_key)).fetchone()
        if not item or item[0] not in ('qq_websocket','qq_official'):
            raise ValueError('group is not an authorized QQ robot source')
    finally:read.close()
    # Initialises only application tables, then makes the mapping atomically.
    store=GroupAccess(account_db)
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT workspace FROM accounts WHERE username=? AND active=1',(admin_username,)).fetchone()
        if not row:raise ValueError('admin account not found')
        if db.execute('SELECT 1 FROM group_access_groups WHERE group_key=?',(group_key,)).fetchone():
            raise ValueError('group already provisioned')
        # Prevent ambiguity: another group's internal key cannot be used as an alias.
        if db.execute('SELECT 1 FROM group_access_groups WHERE group_key=?',(alias,)).fetchone() or db.execute('SELECT 1 FROM group_access_aliases WHERE alias=?',(group_key,)).fetchone():
            raise ValueError('ambiguous group identifier')
        db.execute('INSERT INTO group_access_groups VALUES(?,?,1)',(group_key,name))
        db.execute('INSERT INTO group_access_admins VALUES(?,?)',(group_key,row[0]))
        db.execute('INSERT INTO group_access_aliases VALUES(?,?)',(alias,group_key))
        # A verified group administrator needs access to the same group they review.
        db.execute('INSERT INTO group_access_members VALUES(?,?,1,?,?)',(group_key,row[0],row[0],int(time.time())))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Only after independently verifying the QQ group and admin identity.')
    for arg in ('account-db','message-db','bot-workspace','group-key','alias','name','admin-username'):
        parser.add_argument('--'+arg,required=True)
    x=parser.parse_args()
    provision(x.account_db,x.message_db,x.bot_workspace,x.group_key,x.alias,x.name,x.admin_username)
    print('Group provisioned. Verify access and document the identity check.')
