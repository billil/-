import asyncio, importlib.util, json, sys, tempfile, threading, urllib.request, urllib.error
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent/'member-d/backend'))
from qunyi.config import Config
from qunyi.service import Service
from qunyi.server import Server
spec=importlib.util.spec_from_file_location('qq_group_bot', Path(__file__).parent/'qq-group-bot/qq_group_bot.py')
botmod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=botmod;spec.loader.exec_module(botmod)

def main():
 with tempfile.TemporaryDirectory() as td:
  service=Service(Config(state=Path(td)/'d',tokens={'a'*20:'demo'},qq_workspace='demo',qq_ingest_token='internal-secret'))
  server=Server(('127.0.0.1',0),service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  url=f'http://127.0.0.1:{server.server_port}/v1/internal/qq/messages'
  settings=botmod.Settings('id','secret',frozenset({'group'}),Path(td)/'bot.db',ingest_url=url,ingest_token='internal-secret')
  store=botmod.MessageStore(settings.database_path);bot=botmod.QQGroupBot(settings,store)
  msg={'id':'msg1','group_openid':'group','content':'测试通知','timestamp':'2026-11-01T00:00:00Z'}
  bad=urllib.request.Request(url,data=json.dumps({'group_key':'group','event_id':'bad','content':'x'}).encode(),headers={'Content-Type':'application/json','X-Qunyi-Ingest-Token':'wrong'})
  try: urllib.request.urlopen(bad)
  except urllib.error.HTTPError as e: assert e.code==401
  else: raise AssertionError('bad token accepted')
  bot._save_group_message(msg,'GROUP_AT_MESSAGE_CREATE');bot._save_group_message(msg,'GROUP_AT_MESSAGE_CREATE')
  assert store.connection.execute('select count(*) from outbox').fetchone()[0]==1
  async def send():
   task=asyncio.create_task(bot._outbox_loop())
   for _ in range(100):
    await asyncio.sleep(.05)
    if store.connection.execute('select count(*) from outbox').fetchone()[0]==0:break
   task.cancel()
   try: await task
   except asyncio.CancelledError:pass
  original_url=bot.settings.ingest_url
  object.__setattr__(bot.settings, 'ingest_url', 'http://127.0.0.1:1/v1/internal/qq/messages')
  async def fail_once():
   task=asyncio.create_task(bot._outbox_loop()); await asyncio.sleep(.3); task.cancel()
   try: await task
   except asyncio.CancelledError: pass
  asyncio.run(fail_once())
  assert store.connection.execute('select attempts from outbox').fetchone()[0] >= 1
  store.close(); store=botmod.MessageStore(settings.database_path); bot.store=store
  assert store.connection.execute('select count(*) from outbox').fetchone()[0]==1
  store.connection.execute('update outbox set next_attempt=0'); store.connection.commit()
  object.__setattr__(bot.settings, 'ingest_url', original_url)
  asyncio.run(send())
  assert store.connection.execute('select count(*) from outbox').fetchone()[0]==0
  bot._forward_to_qunyi(msg,'GROUP_AT_MESSAGE_CREATE')
  assert service.store.one('select count(*) n from messages')['n']==1
  server.shutdown();server.server_close();store.close()
  print('HTTP authentication, outage retry, restart recovery and deduplication: PASS')
if __name__=='__main__': main()
