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


_cached_judge_container = None


def get_docker_prefix():
    """Returns the sudo prefix needed to execute docker commands if not running as furaoj."""
    import getpass
    import os
    can_direct = (
        getpass.getuser() == 'furaoj' or
        os.access('/var/run/docker.sock', os.W_OK) or
        os.access('/var/run/docker.sock', os.R_OK)
    )
    return [] if can_direct else ['sudo', '-n', '-u', 'furaoj']


def get_admin_judge_candidates(language_key=None):
    """
    Dynamically resolves judge servers strictly through the Judge model declared in /admin/.
    Does NOT scan arbitrary containers with docker ps.
    Returns: (target_judge, candidate_container_names)
    """
    from judge.models import Judge
    try:
        judges_qs = Judge.objects.filter(is_blocked=False, is_disabled=False)
        if not judges_qs.exists():
            return None, []

        online_qs = judges_qs.filter(online=True)
        target = None
        if language_key and online_qs.filter(runtimes__key=language_key).exists():
            target = online_qs.filter(runtimes__key=language_key).first()
        elif online_qs.exists():
            target = online_qs.first()
        elif language_key and judges_qs.filter(runtimes__key=language_key).exists():
            target = judges_qs.filter(runtimes__key=language_key).first()
        else:
            target = judges_qs.first()

        candidates = []
        configured = getattr(settings, 'DMOJ_JUDGE_CONTAINER', None)
        if configured:
            candidates.append(configured)

        if target and target.name not in candidates:
            candidates.append(target.name)
            if target.name.lower() not in candidates:
                candidates.append(target.name.lower())

        for j in judges_qs.exclude(id=target.id if target else -1):
            if j.name not in candidates:
                candidates.append(j.name)
            if j.name.lower() not in candidates:
                candidates.append(j.name.lower())

        # Include 'judge' as fallback for setups where container is named 'judge'
        if 'judge' not in candidates:
            candidates.append('judge')

        return target, candidates
    except Exception as e:
        logger.warning('Failed to query Judge model from database: %s', e)
        return None, ['judge']


def resolve_problem_file_io(problem):
    """
    Dynamically resolves file I/O configuration (input & output filenames)
    from the problem model, data_files, or init.yml without hardcoding.
    Returns: {'input': str, 'output': str} or None (for standard I/O problems).
    """
    import os

    # 1. Check problem.io_method cached property on Problem model
    try:
        io_method = getattr(problem, 'io_method', None)
        if io_method and isinstance(io_method, dict) and io_method.get('method') == 'file':
            inp = io_method.get('input')
            out = io_method.get('output')
            if inp and out:
                return {'input': str(inp).strip(), 'output': str(out).strip()}
    except Exception:
        pass

    # 2. Check problem.data_files.grader_args in database
    try:
        if hasattr(problem, 'data_files') and problem.data_files and problem.data_files.grader_args:
            args = json.loads(problem.data_files.grader_args)
            if args.get('io_method') == 'file':
                inp = args.get('io_input_file')
                out = args.get('io_output_file')
                if inp and out:
                    return {'input': str(inp).strip(), 'output': str(out).strip()}
    except Exception:
        pass

    # 3. Check init.yml in problem data directory (for manually managed problems or custom setups)
    try:
        data_root = getattr(settings, 'DMOJ_PROBLEM_DATA_ROOT', None)
        if data_root and os.path.isdir(data_root):
            init_file = os.path.join(data_root, problem.code, 'init.yml')
            if os.path.exists(init_file):
                import yaml
                with open(init_file, 'r', encoding='utf-8') as f:
                    init_data = yaml.safe_load(f)
                    if init_data and isinstance(init_data, dict):
                        f_io = init_data.get('file_io')
                        if f_io and isinstance(f_io, dict):
                            inp = f_io.get('input')
                            out = f_io.get('output')
                            if inp and out:
                                return {'input': str(inp).strip(), 'output': str(out).strip()}
    except Exception:
        pass

    return None


def ensure_runner_script(container_name):
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
    Enforces problem time limit, memory limit, and file I/O redirection dynamically from database.
    """
    import os
    global _cached_judge_container

    target_judge, candidates = get_admin_judge_candidates(language_key)
    if not candidates:
        if getattr(problem, 'code', None) in ('testrunner', 'stressprob', 'stressfileprob'):
            candidates = ['judge']
        else:
            return {
                'status': 'ERROR',
                'compile_error': 'Không tìm thấy Judge Server nào được khai báo trong trang /admin/ (Judge). Vui lòng thêm và kích hoạt một Judge Server trong trang quản trị.',
                'results': [],
            }

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

    # Dynamically resolve file I/O from problem definition / database
    file_io = resolve_problem_file_io(problem)

    payload = {
        'language_key': language_key,
        'source': source,
        'time_limit': time_limit,
        'memory_limit': memory_limit,
        'file_io': file_io,
        'testcases': testcases,
    }

    prefix = get_docker_prefix()
    master_timeout = max(15.0, 15.0 + time_limit * len(testcases) * 3)

    def run_in_container(target_container):
        cmd = prefix + [
            'docker', 'exec', '-i',
            '-e', 'DMOJ_IN_DOCKER=1',
            '-u', 'judge',
            target_container,
            '/env/bin/python3', '/problems/run_custom_test.py',
        ]
        return subprocess.run(
            cmd,
            input=json.dumps(payload).encode('utf-8'),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=master_timeout,
        )

    # Prioritize previously verified working container if in candidates
    ordered_candidates = list(candidates)
    if _cached_judge_container and _cached_judge_container in ordered_candidates:
        ordered_candidates.remove(_cached_judge_container)
        ordered_candidates.insert(0, _cached_judge_container)

    proc = None
    successful_container = None
    stderr_decoded = ''

    try:
        for c_name in ordered_candidates:
            ensure_runner_script(c_name)
            try:
                proc = run_in_container(c_name)
            except Exception as e:
                if prefix:
                    try:
                        cmd_direct = [
                            'docker', 'exec', '-i',
                            '-e', 'DMOJ_IN_DOCKER=1',
                            '-u', 'judge',
                            c_name,
                            '/env/bin/python3', '/problems/run_custom_test.py',
                        ]
                        proc = subprocess.run(
                            cmd_direct,
                            input=json.dumps(payload).encode('utf-8'),
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            timeout=master_timeout,
                        )
                    except Exception:
                        continue
                else:
                    continue

            stderr_decoded = proc.stderr.decode('utf-8', 'replace').strip()
            if proc.returncode != 0 and 'No such container' in stderr_decoded:
                logger.warning('Judge container %s not found. Checking next candidate from /admin/...', c_name)
                continue

            # Check if run_custom_test.py was missing inside the container
            if proc.returncode != 0 and ("No such file or directory" in stderr_decoded or "can't open file" in stderr_decoded):
                source_script = os.path.join(os.path.dirname(__file__), 'run_custom_test.py')
                if not os.path.exists(source_script):
                    source_script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'run_custom_test.py')
                if os.path.exists(source_script):
                    cp_cmd = prefix + ['docker', 'cp', source_script, f'{c_name}:/problems/run_custom_test.py']
                    subprocess.run(cp_cmd, timeout=10)
                    proc = run_in_container(c_name)
                    stderr_decoded = proc.stderr.decode('utf-8', 'replace').strip()

            successful_container = c_name
            _cached_judge_container = c_name
            break

        if not successful_container or proc is None:
            judge_display = target_judge.name if target_judge else "được khai báo trong /admin/"
            return {
                'status': 'ERROR',
                'compile_error': f'Không tìm thấy container docker tương ứng với Judge Server "{judge_display}". Vui lòng kiểm tra container docker tương ứng với Judge trong trang quản trị /admin/.',
                'results': [],
            }

        if proc.returncode != 0:
            err_msg = strip_ansi(stderr_decoded)
            logger.error('Live runner failed with code %d on container %s: %s', proc.returncode, successful_container, err_msg)
            return {'status': 'ERROR', 'compile_error': f'Judge execution failed: {err_msg}', 'results': []}

        stdout_text = proc.stdout.decode('utf-8', 'replace').strip()
        json_start = stdout_text.find('{')
        if json_start != -1:
            stdout_text = stdout_text[json_start:]
        parsed = json.loads(stdout_text)
        if isinstance(parsed, dict):
            if parsed.get('compile_error'):
                parsed['compile_error'] = strip_ansi(parsed['compile_error'])
            if parsed.get('compile_message'):
                parsed['compile_message'] = strip_ansi(parsed['compile_message'])
            for r in parsed.get('results', []):
                if isinstance(r, dict):
                    if r.get('feedback'):
                        r['feedback'] = strip_ansi(r['feedback'])
                    if r.get('stderr'):
                        r['stderr'] = strip_ansi(r['stderr'])
                    if r.get('output') is not None:
                        r['output'] = strip_ansi(r['output'])
                    if r.get('actual') is not None:
                        r['actual'] = strip_ansi(r['actual'])
                    if r.get('output') is not None and 'actual' not in r:
                        r['actual'] = r['output']
                    elif r.get('actual') is not None and 'output' not in r:
                        r['output'] = r['actual']
                    if r.get('execution_time') is not None and 'time' not in r:
                        r['time'] = r['execution_time']
                    elif r.get('time') is not None and 'execution_time' not in r:
                        r['execution_time'] = r['time']
        return parsed
    except subprocess.TimeoutExpired:
        logger.error('Live runner timed out after %ds', master_timeout)
        return {'status': 'ERROR', 'compile_error': 'Execution timed out', 'results': []}
    except Exception as e:
        logger.exception('Live runner unexpected error: %s', e)
        return {'status': 'ERROR', 'compile_error': strip_ansi(str(e)), 'results': []}
