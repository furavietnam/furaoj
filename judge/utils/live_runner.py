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


def resolve_judge_container(force_refresh=False):
    """
    Dynamically discovers the running judge container without hardcoding:
    1. settings.DMOJ_JUDGE_CONTAINER or environment variable DMOJ_JUDGE_CONTAINER
    2. Matches active/configured Judge models from database against docker ps
    3. Searches docker ps for running containers matching 'judge', 'vnoj', or 'dmoj'
    4. If only 1 container is running on docker, uses it
    5. Fallback to Judge model name from database, or 'judge'
    """
    global _cached_judge_container
    import os
    if not force_refresh and _cached_judge_container:
        return _cached_judge_container

    configured = getattr(settings, 'DMOJ_JUDGE_CONTAINER', None) or os.environ.get('DMOJ_JUDGE_CONTAINER')
    if configured:
        _cached_judge_container = configured
        return configured

    prefix = get_docker_prefix()
    try:
        res = subprocess.run(
            prefix + ['docker', 'ps', '--format', '{{.Names}}\t{{.Image}}'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = [line.strip().split('\t') for line in res.stdout.strip().splitlines() if line.strip()]
            containers = [(parts[0], parts[1] if len(parts) > 1 else '') for parts in lines if parts]

            # A. Match active or configured Judge names in Django database
            try:
                from judge.models import Judge
                db_names = list(Judge.objects.filter(online=True).values_list('name', flat=True)) + \
                           list(Judge.objects.all().values_list('name', flat=True))
                db_names_lower = [d.lower() for d in db_names]
                for name, img in containers:
                    if name in db_names or name.lower() in db_names_lower:
                        _cached_judge_container = name
                        return name
            except Exception:
                pass

            # B. Match container name containing 'judge' (case-insensitive)
            for name, img in containers:
                if 'judge' in name.lower():
                    _cached_judge_container = name
                    return name

            # C. Match container image containing 'judge', 'vnoj', or 'dmoj'
            for name, img in containers:
                if any(k in img.lower() for k in ['judge', 'vnoj', 'dmoj']):
                    _cached_judge_container = name
                    return name

            # D. If exactly 1 container is running on the host, use it
            if len(containers) == 1:
                _cached_judge_container = containers[0][0]
                return containers[0][0]

            logger.info('Discovered running docker containers: %s', [c[0] for c in containers])
    except Exception as e:
        logger.warning('Failed to query docker containers: %s', e)

    # 3. Fallback to Judge model name from database
    try:
        from judge.models import Judge
        active = Judge.objects.filter(online=True).first() or Judge.objects.first()
        if active:
            _cached_judge_container = active.name
            return active.name
    except Exception:
        pass

    _cached_judge_container = 'judge'
    return 'judge'


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

    container_name = resolve_judge_container()
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

    try:
        try:
            proc = run_in_container(container_name)
        except Exception as e:
            if prefix:
                # If sudo failed, try running without sudo directly
                cmd_direct = [
                    'docker', 'exec', '-i',
                    '-e', 'DMOJ_IN_DOCKER=1',
                    '-u', 'judge',
                    container_name,
                    '/env/bin/python3', '/problems/run_custom_test.py',
                ]
                proc = subprocess.run(
                    cmd_direct,
                    input=json.dumps(payload).encode('utf-8'),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=master_timeout,
                )
            else:
                raise e

        # Check if container name failed (e.g. No such container)
        stderr_decoded = proc.stderr.decode('utf-8', 'replace').strip()
        if proc.returncode != 0 and 'No such container' in stderr_decoded:
            logger.warning('Container %s not found. Re-probing docker containers...', container_name)
            new_container = resolve_judge_container(force_refresh=True)
            if new_container and new_container != container_name:
                container_name = new_container
                proc = run_in_container(container_name)
                stderr_decoded = proc.stderr.decode('utf-8', 'replace').strip()

        # Check if run_custom_test.py was missing inside the container
        if proc.returncode != 0 and ("No such file or directory" in stderr_decoded or "can't open file" in stderr_decoded):
            source_script = os.path.join(os.path.dirname(__file__), 'run_custom_test.py')
            if not os.path.exists(source_script):
                source_script = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'run_custom_test.py')
            if os.path.exists(source_script):
                cp_cmd = prefix + ['docker', 'cp', source_script, f'{container_name}:/problems/run_custom_test.py']
                subprocess.run(cp_cmd, timeout=10)
                # Retry execution once
                proc = run_in_container(container_name)

        if proc.returncode != 0:
            err_msg = strip_ansi(proc.stderr.decode('utf-8', 'replace').strip())
            logger.error('Live runner failed with code %d on container %s: %s', proc.returncode, container_name, err_msg)
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
