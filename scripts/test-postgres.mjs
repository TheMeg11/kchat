// Runs ONLY isolated integration tests; never uses the deployment database.
import EmbeddedPostgres from 'embedded-postgres';
import {spawn} from 'node:child_process';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const directory = await mkdtemp(join(tmpdir(), 'hamchat-pg-'));
const pg = new EmbeddedPostgres({databaseDir:directory, user:'postgres',password:'local-test-only',port:55432,persistent:false,onLog:()=>{},onError:console.error});
try {
  await pg.initialise(); await pg.start(); await pg.createDatabase('hamchat_test');
  const result = await new Promise((resolve,reject)=>{
    const child = spawn('.venv/bin/python',['-m','pytest','tests/test_postgres.py','-q'],{stdio:'inherit',env:{...process.env,TEST_PG_URL:'postgresql+asyncpg://postgres:local-test-only@127.0.0.1:55432/hamchat_test'}});
    child.on('exit',resolve);child.on('error',reject);
  });
  process.exitCode = result;
} finally { await pg.stop(); }
