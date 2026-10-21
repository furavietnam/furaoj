#!/usr/bin/env python3
import json
import os
import re
import subprocess
import sys

ANSI_ESCAPE_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def strip_ansi(text):
    if not text:
        return ''
    return ANSI_ESCAPE_RE.sub('', str(text))

os.environ['DMOJ_IN_DOCKER'] = '1'

# Prepare runtime config to prevent "no problems available to grade" SystemExit
live_cfg_path = '/tmp/live_judge.yml'
config_file = '/problems/judge.yml'
try:
    cfg = {}
    if os.path.exists('/problems/judge.yml'):
        try:
            import yaml
            with open('/problems/judge.yml', 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}
    if not isinstance(cfg, dict):
        cfg = {}
    if not cfg.get('problem_storage_globs'):
        cfg['problem_storage_globs'] = ['/problems/*', '/tmp/*']
    if 'id' not in cfg:
        cfg['id'] = 'live'
    if 'key' not in cfg:
        cfg['key'] = 'live'
    try:
        import yaml
        with open(live_cfg_path, 'w', encoding='utf-8') as f:
            yaml.safe_dump(cfg, f)
        config_file = live_cfg_path
    except Exception:
        pass
except Exception:
    pass

# Suppress stderr noise during initialization
sys.argv = ['dmoj', '-c', config_file, '--skip-self-test', 'localhost']

try:
    # Redirect stdout during dmoj init to keep stdout clean for JSON output
    real_stdout = sys.stdout
    with open(os.devnull, 'w') as devnull:
        sys.stdout = devnull
        from dmoj import executors, judgeenv
        from dmoj.config import ConfigNode
        from dmoj.cptbox.utils import MemoryIO
        from dmoj.error import CompileError, OutputLimitExceeded
        from dmoj.result import Result
        from dmoj.utils.unicode import utf8bytes

        try:
            judgeenv.load_env()
        except SystemExit as se:
            if 'no problems available to grade' in str(se):
                judgeenv.env.problem_storage_globs = ['/problems/*', '/tmp/*']
                judgeenv.storage_namespaces[None] = ['/problems/*', '/tmp/*']
            else:
                raise se
        except Exception:
            pass

        # Ensure problem storage namespaces are valid even if empty
        if not getattr(judgeenv.env, 'problem_storage_globs', None):
            judgeenv.env.problem_storage_globs = ['/problems/*', '/tmp/*']
        if not getattr(judgeenv, 'storage_namespaces', None) or not judgeenv.storage_namespaces.get(None):
            judgeenv.storage_namespaces[None] = ['/problems/*', '/tmp/*']

        executors.load_executors()
    sys.stdout = real_stdout
except BaseException as e:
    sys.stdout = real_stdout
    sys.stdout.write(json.dumps({
        'status': 'ERROR',
        'compile_error': f'Failed to initialize judge environment: {e}',
        'results': [],
    }))
    sys.exit(0)


def main():
    try:
        raw_input = sys.stdin.read()
        if not raw_input:
            sys.stdout.write(json.dumps({'status': 'ERROR', 'compile_error': 'Empty request', 'results': []}))
            return
        data = json.loads(raw_input)
    except Exception as e:
        sys.stdout.write(json.dumps({'status': 'ERROR', 'compile_error': f'Invalid JSON payload: {e}', 'results': []}))
        return

    lang_key = data.get('language_key')
    source = data.get('source', '')
    time_limit = float(data.get('time_limit', 1.0))
    memory_limit = int(data.get('memory_limit', 65536))
    file_io_data = data.get('file_io')
    testcases = data.get('testcases', [])[:5]

    if lang_key not in executors.executors:
        sys.stdout.write(json.dumps({
            'status': 'ERROR',
            'compile_error': f'Unsupported language: {lang_key}',
            'results': [],
        }))
        return

    ExecutorClass = executors.executors[lang_key].Executor
    try:
        executor = ExecutorClass('live_run', utf8bytes(source))
    except CompileError as e:
        sys.stdout.write(json.dumps({
            'status': 'CE',
            'compile_error': strip_ansi(e.message or e),
            'results': [],
        }))
        return
    except Exception as e:
        sys.stdout.write(json.dumps({
            'status': 'CE',
            'compile_error': strip_ansi(f'Compilation error: {e}'),
            'results': [],
        }))
        return

    config_file_io = None
    symlinks = {}
    if file_io_data and isinstance(file_io_data, dict):
        inp_file = file_io_data.get('input')
        out_file = file_io_data.get('output')
        if inp_file and out_file:
            config_file_io = ConfigNode({'input': inp_file, 'output': out_file})
            if inp_file.lower() != inp_file:
                symlinks[inp_file.lower()] = inp_file
            if inp_file.upper() != inp_file:
                symlinks[inp_file.upper()] = inp_file
            if out_file.lower() != out_file:
                symlinks[out_file.lower()] = out_file
            if out_file.upper() != out_file:
                symlinks[out_file.upper()] = out_file

    results = []
    try:
        for tc in testcases:
            tc_id = tc.get('id', len(results) + 1)
            inp = tc.get('input', '')
            exp = tc.get('expected')

            try:
                with MemoryIO(prefill=utf8bytes(inp), seal=True) as input_stream:
                    proc = executor.launch(
                        time=time_limit,
                        memory=memory_limit,
                        file_io=config_file_io,
                        symlinks=symlinks,
                        stdin=input_stream,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        wall_time=max(2.0, time_limit * 3),
                    )
                    try:
                        out, err = proc.communicate(None, outlimit=65536, errlimit=65536)
                    except OutputLimitExceeded:
                        try:
                            proc.kill()
                            proc.wait()
                        except Exception:
                            pass
                        ole_time = round(proc.execution_time or 0.0, 3)
                        results.append({
                            'id': tc_id,
                            'status': 'OLE',
                            'time': ole_time,
                            'execution_time': ole_time,
                            'memory': int(proc.max_memory or 0),
                            'output': '[Output Limit Exceeded - Output truncated]',
                            'actual': '[Output Limit Exceeded - Output truncated]',
                            'expected': exp if exp is not None else '',
                            'feedback': 'Output limit exceeded (max 64KB)',
                            'stderr': '',
                        })
                        continue

                    out_str = out.decode('utf-8', 'replace')
                    err_str = err.decode('utf-8', 'replace')

                    # If out_str is empty and file I/O is configured, check if file was written directly in sandbox
                    if (not out_str or not out_str.strip()) and hasattr(executor, '_dir') and executor._dir and file_io_data:
                        out_target = file_io_data.get('output', '')
                        candidates = [out_target, out_target.lower(), out_target.upper()]
                        for cand in candidates:
                            if not cand:
                                continue
                            cand_path = os.path.join(executor._dir, cand)
                            if os.path.isfile(cand_path) and not os.path.islink(cand_path):
                                try:
                                    with open(cand_path, 'r', encoding='utf-8', errors='replace') as f:
                                        file_content = f.read(65536)
                                        if file_content:
                                            out_str = file_content
                                            break
                                except Exception:
                                    pass

                    feedback = ''
                    is_rte_or_ir = proc.is_rte or proc.is_ir or (proc.returncode is not None and proc.returncode != 0)

                    if proc.is_tle:
                        status = 'TLE'
                        feedback = 'Time Limit Exceeded'
                    elif proc.is_mle:
                        status = 'MLE'
                        feedback = 'Memory Limit Exceeded'
                    elif proc.is_ole:
                        status = 'OLE'
                        feedback = 'Output Limit Exceeded'
                    elif is_rte_or_ir:
                        status = 'RTE'
                        try:
                            feedback = Result.get_feedback_str(err, proc, executor)
                        except Exception:
                            feedback = ''

                        if not feedback:
                            try:
                                feedback = executor.parse_feedback_from_stderr(err, proc)
                            except Exception:
                                feedback = ''

                        if not feedback:
                            if getattr(proc, 'protection_fault', None):
                                try:
                                    syscall, callname, args, update_errno = proc.protection_fault
                                    callname = callname.replace('sys_', '', 1)
                                    feedback = f'{callname} syscall disallowed'
                                except Exception:
                                    feedback = 'Disallowed syscall'
                            elif getattr(proc, 'signal', None):
                                try:
                                    import signal
                                    sig_desc = signal.strsignal(proc.signal)
                                    feedback = sig_desc if sig_desc else f'Signal {proc.signal}'
                                except Exception:
                                    feedback = f'Signal {proc.signal}'
                            elif proc.returncode is not None and proc.returncode != 0:
                                feedback = f'Exit code {proc.returncode}'
                    else:
                        if exp is not None and str(exp).strip() != '':
                            norm_out = out_str.strip().split()
                            norm_exp = str(exp).strip().split()
                            status = 'AC' if norm_out == norm_exp else 'WA'
                        else:
                            status = 'EXECUTED'

                    clean_out = strip_ansi(out_str)
                    clean_feedback = strip_ansi(feedback)
                    clean_stderr = strip_ansi(err_str)
                    exec_time = round(proc.execution_time or 0.0, 3)

                    results.append({
                        'id': tc_id,
                        'status': status,
                        'time': exec_time,
                        'execution_time': exec_time,
                        'memory': int(proc.max_memory or 0),
                        'output': clean_out,
                        'actual': clean_out,
                        'expected': exp if exp is not None else '',
                        'feedback': clean_feedback,
                        'stderr': clean_stderr,
                    })
            except Exception as ex:
                results.append({
                    'id': tc_id,
                    'status': 'RTE',
                    'time': 0.0,
                    'execution_time': 0.0,
                    'memory': 0,
                    'output': '',
                    'actual': '',
                    'expected': exp if exp is not None else '',
                    'feedback': strip_ansi(f'Execution failed: {ex}'),
                    'stderr': strip_ansi(str(ex)),
                })
    finally:
        try:
            executor.cleanup()
        except Exception:
            pass

    compile_msg = None
    if hasattr(executor, 'warning') and executor.warning:
        try:
            compile_msg = strip_ansi(executor.warning.decode('utf-8', 'replace'))
        except Exception:
            pass

    sys.stdout.write(json.dumps({
        'status': 'OK',
        'compile_error': None,
        'compile_message': compile_msg,
        'results': results,
    }))


if __name__ == '__main__':
    main()
