import json
import logging
import subprocess
from django.conf import settings
from judge.models import LanguageLimit

logger = logging.getLogger('judge.live_runner')


def execute_live_run(problem, language_key, source, testcases):
    """
    Executes source code against custom testcases inside the judge sandbox.
    Enforces problem time limit, memory limit, and file I/O redirection if configured.
    """
    if len(source.encode('utf-8')) > 1048576:
        return {'status': 'ERROR', 'compile_error': 'Source code exceeds maximum limit of 1MB.', 'results': []}

    testcases = testcases[:5]

    time_limit = float(problem.time_limit)
    memory_limit = int(problem.memory_limit)

    # Check language-specific limits if any
    try:
        lang_limit = LanguageLimit.objects.filter(problem=problem, language__key=language_key).first()
        if lang_limit:
            if lang_limit.time_limit:
                time_limit = float(lang_limit.time_limit)
            if lang_limit.memory_limit:
                memory_limit = int(lang_limit.memory_limit)
    except Exception:
        pass

    file_io = None
    if getattr(problem, 'io_method', None) and problem.io_method.get('method') == 'file':
        file_io = {
            'input': problem.io_method.get('input'),
            'output': problem.io_method.get('output'),
        }

    payload = {
        'language_key': language_key,
        'source': source,
        'time_limit': time_limit,
        'memory_limit': memory_limit,
        'file_io': file_io,
        'testcases': testcases,
    }

    cmd = [
        'sudo', '-u', 'furaoj', 'docker', 'exec', '-i',
        '-e', 'DMOJ_IN_DOCKER=1',
        '-u', 'judge',
        'judge',
        '/env/bin/python3', '/problems/run_custom_test.py',
    ]

    master_timeout = max(15.0, 15.0 + time_limit * len(testcases) * 3)

    try:
        proc = subprocess.run(
            cmd,
            input=json.dumps(payload).encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=master_timeout,
        )
        if proc.returncode != 0:
            err_msg = proc.stderr.decode('utf-8', 'replace').strip()
            logger.error('Live runner failed with code %d: %s', proc.returncode, err_msg)
            return {'status': 'ERROR', 'compile_error': f'Judge execution failed: {err_msg}', 'results': []}

        stdout_text = proc.stdout.decode('utf-8', 'replace').strip()
        json_start = stdout_text.find('{')
        if json_start != -1:
            stdout_text = stdout_text[json_start:]
        return json.loads(stdout_text)
    except subprocess.TimeoutExpired:
        logger.error('Live runner timed out after %ds', master_timeout)
        return {'status': 'ERROR', 'compile_error': 'Execution timed out', 'results': []}
    except Exception as e:
        logger.exception('Live runner unexpected error: %s', e)
        return {'status': 'ERROR', 'compile_error': str(e), 'results': []}
