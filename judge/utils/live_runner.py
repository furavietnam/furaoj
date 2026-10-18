import json
import logging
import re
import subprocess
from django.conf import settings
from judge.models import LanguageLimit

logger = logging.getLogger('judge.live_runner')

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_ansi(text):
    if not text:
        return ''
    return ANSI_ESCAPE_RE.sub('', str(text))


def ensure_runner_script(container_name='judge'):
    """
    Ensures run_custom_test.py is available in DMOJ_PROBLEM_DATA_ROOT and inside the container.
    """
    import os
    source = os.path.join(os.path.dirname(__file__), 'run_custom_test.py')
    if not os.path.exists(source):
        source = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'run_custom_test.py')
    if not os.path.exists(source):
        return

    # 1. Sync to DMOJ_PROBLEM_DATA_ROOT on host
    try:
        data_root = getattr(settings, 'DMOJ_PROBLEM_DATA_ROOT', None)
        if data_root and os.path.isdir(data_root):
            target = os.path.join(data_root, 'run_custom_test.py')
            need_copy = not os.path.exists(target)
            if not need_copy:
                try:
                    with open(source, 'rb') as f1, open(target, 'rb') as f2:
                        if f1.read() != f2.read():
                            need_copy = True
                except Exception:
                    need_copy = True
            if need_copy:
                import shutil
                shutil.copyfile(source, target)
                os.chmod(target, 0o755)
    except Exception as e:
        logger.warning('Failed to sync run_custom_test.py to DMOJ_PROBLEM_DATA_ROOT: %s', e)


def execute_live_run(problem, language_key, source, testcases):
    """
    Executes source code against custom testcases inside the judge sandbox.
    Enforces problem time limit, memory limit, and file I/O redirection if configured.
    """
    import os
    import getpass

    container_name = getattr(settings, 'DMOJ_JUDGE_CONTAINER', 'judge')
    ensure_runner_script(container_name)

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

    base_cmd = [
        'docker', 'exec', '-i',
        '-e', 'DMOJ_IN_DOCKER=1',
        '-u', 'judge',
        container_name,
        '/env/bin/python3', '/problems/run_custom_test.py',
    ]

    can_direct_docker = (
        getpass.getuser() == 'furaoj' or
        os.access('/var/run/docker.sock', os.W_OK) or
        os.access('/var/run/docker.sock', os.R_OK)
    )

    if can_direct_docker:
        cmd = base_cmd
    else:
        # Use -n (non-interactive) so sudo never blocks indefinitely waiting for password
        cmd = ['sudo', '-n', '-u', 'furaoj'] + base_cmd

    master_timeout = max(15.0, 15.0 + time_limit * len(testcases) * 3)

    def run_proc(exec_cmd):
        return subprocess.run(
            exec_cmd,
            input=json.dumps(payload).encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=master_timeout,
        )

    try:
        try:
            proc = run_proc(cmd)
        except Exception as e:
            if cmd != base_cmd:
                # If sudo failed, attempt direct docker as fallback
                proc = run_proc(base_cmd)
            else:
                raise e

        # Check if run_custom_test.py was missing inside the container
        stderr_decoded = proc.stderr.decode('utf-8', 'replace').strip()
        if proc.returncode != 0 and ("No such file or directory" in stderr_decoded or "can't open file" in stderr_decoded):
            # Attempt to copy script into container using docker cp
            source_script = os.path.join(os.path.dirname(__file__), 'run_custom_test.py')
            if not os.path.exists(source_script):
                source_script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'run_custom_test.py')
            if os.path.exists(source_script):
                cp_prefix = [] if can_direct_docker else ['sudo', '-n', '-u', 'furaoj']
                cp_cmd = cp_prefix + ['docker', 'cp', source_script, f'{container_name}:/problems/run_custom_test.py']
                subprocess.run(cp_cmd, timeout=10)
                # Retry execution once
                proc = run_proc(cmd)

        if proc.returncode != 0:
            err_msg = strip_ansi(proc.stderr.decode('utf-8', 'replace').strip())
            logger.error('Live runner failed with code %d: %s', proc.returncode, err_msg)
            return {'status': 'ERROR', 'compile_error': f'Judge execution failed: {err_msg}', 'results': []}

        stdout_text = proc.stdout.decode('utf-8', 'replace').strip()
        json_start = stdout_text.find('{')
        if json_start != -1:
            stdout_text = stdout_text[json_start:]
        parsed = json.loads(stdout_text)
        if isinstance(parsed, dict):
            if parsed.get('compile_error'):
                parsed['compile_error'] = strip_ansi(parsed['compile_error'])
            for r in parsed.get('results', []):
                if isinstance(r, dict) and r.get('feedback'):
                    r['feedback'] = strip_ansi(r['feedback'])
        return parsed
    except subprocess.TimeoutExpired:
        logger.error('Live runner timed out after %ds', master_timeout)
        return {'status': 'ERROR', 'compile_error': 'Execution timed out', 'results': []}
    except Exception as e:
        logger.exception('Live runner unexpected error: %s', e)
        return {'status': 'ERROR', 'compile_error': strip_ansi(str(e)), 'results': []}
