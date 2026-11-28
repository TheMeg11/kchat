"""Regression for INFO startup messages being classified as Railway errors."""
import json
import logging
from pathlib import Path
import subprocess
import sys

import pytest
from app.logging_config import RailwayJSONFormatter, log_config


@pytest.mark.parametrize('level,expected',[(logging.DEBUG,'debug'),(logging.INFO,'info'),
    (logging.WARNING,'warn'),(logging.ERROR,'error'),(logging.CRITICAL,'fatal')])
def test_formatter_preserves_real_level_and_single_json_line(level,expected):
    record=logging.LogRecord('kanar.main',level,__file__,1,'سلام %s\nnext line',('دوست',),None)
    text=RailwayJSONFormatter().format(record)
    result=json.loads(text)
    assert result['level']==expected
    assert result['logger']=='kanar.main'
    assert result['timestamp'].endswith('Z')
    assert result['message']=='سلام دوست\nnext line'
    assert '\n' not in text  # escaped in JSON, not several physical log lines
    assert record.msg=='سلام %s\nnext line' and record.args==('دوست',)


def test_tokens_and_webhook_paths_redacted_even_in_exceptions():
    token='123456:TEST_ONLY_FAKE_TOKEN'
    webhook='TEST_ONLY_PRIVATE_WEBHOOK_PATH'
    try:
        raise RuntimeError(f'POST https://tapi.bale.ai/bot{token}/getUpdates; https://example.test/webhook/bale/{webhook}')
    except RuntimeError:
        record=logging.LogRecord('kanar.main',logging.ERROR,__file__,1,
            'Failed %s',(f'https://api.telegram.org/file/bot{token}/photo.jpg',),sys.exc_info())
    text=RailwayJSONFormatter().format(record)
    assert token not in text and webhook not in text
    result=json.loads(text)
    assert 'Traceback' in result['message'] and 'RuntimeError' in result['message']
    assert result['level']=='error' and 'REDACTED' in result['message']
    assert '\n' not in text


def test_uvicorn_and_app_use_json_stdout_no_duplicates_or_info_http_noise():
    # A child interpreter avoids replacing pytest's own logging/capture setup.
    code='''
import logging, logging.config
from app.logging_config import log_config
from uvicorn import Config
Config('app.main:app', log_config=log_config(), log_level='info', access_log=False)
logging.getLogger('kanar.main').info('database OK')
logging.getLogger('uvicorn.error').info('Application startup complete.')
logging.getLogger('uvicorn.error').error('real server error')
logging.getLogger('httpx').info('noisy successful request')
logging.getLogger('httpx').warning('HTTP timeout https://example.test/bot123:FAKE_TOKEN/getUpdates')
'''
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,check=True,cwd=Path(__file__).resolve().parents[1])
    assert result.stderr==''
    lines=[json.loads(line) for line in result.stdout.splitlines()]
    assert [line['level'] for line in lines]==['info','info','error','warn']
    assert [line['logger'] for line in lines]==['kanar.main','uvicorn.error','uvicorn.error','httpx']
    assert 'noisy' not in result.stdout and 'FAKE_TOKEN' not in result.stdout


def test_entrypoint_passes_structured_config_without_changing_server_limits(monkeypatch):
    from app import main
    calls=[]
    monkeypatch.setattr(main.uvicorn,'run',lambda *args,**kwargs:calls.append((args,kwargs)))
    main.main()
    args,kwargs=calls[0]
    assert args==('app.main:app',)
    assert kwargs['log_config']==log_config()
    assert kwargs['host']=='0.0.0.0' and kwargs['proxy_headers'] is False
    assert kwargs['access_log'] is False and kwargs['limit_concurrency']==100
