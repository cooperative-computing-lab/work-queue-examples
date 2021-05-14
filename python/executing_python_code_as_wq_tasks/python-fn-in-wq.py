################################################################################
#
# install work queue cctools and dependencies:
#
# conda create --yes --name work_queue_environment python=3.8 dill
# conda install --yes --name work_queue_environment --channel conda-forge ndcctools conda-pack
# conda activate work_queue_environment
# python python-fn-in-wq.py
#

import dill
import logging
import os
from os.path import basename
import shutil
import subprocess
import sys
import tempfile

import work_queue as wq

logger = logging.getLogger()
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s:%(levelname)s:%(message)s')


### Python environment creation

def create_python_environment(filename_template='python-env-{}.tar.gz', python_version=3.8, force=False):

    filename=filename_template.format(python_version)

    if os.path.exists(filename) and not force:
        logger.debug('environment file {} already exists, not recreating'.format(filename))
        return filename

    logger.debug('creating environment file {}'.format(filename))
    with tempfile.TemporaryDirectory() as tmp_env:
        run_conda_command(tmp_env, 'create', 'python={}'.format(python_version)),
        run_conda_command(tmp_env, 'install', 'conda', 'dill')
        run_conda_command(tmp_env, 'install', '--channel=conda-forge', 'conda-pack')
        #run_conda_command(tmp_env, 'install', 'some othe packages')
        #run_conda_command(tmp_env, 'run', 'python', '-mpip', 'install', 'some pip package')

        run_conda_command(tmp_env, 'run', 'conda-pack', '-p', tmp_env, '-o', filename)

    logger.debug('environment file {} was created'.format(filename))
    return filename


def run_conda_command(env_dir, cmd, *args):
    try:
        base_args=['conda', cmd, '-p', env_dir]
        if cmd != 'run':
            base_args.append('--yes')
        all_args = base_args + list(args)
        logger.debug('conda cmd: {}'.format(' '.join(all_args)))
        subprocess.check_output(all_args, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as e:
        logger.error('conda command error: {}'.format(e))
        print(e.output)
        sys.exit(1)



### Transforming python functions to work queue tasks
def create_function_wrapper(tmpdir):
    """Writes a wrapper script to run dilled python functions and arguments.
    The wrapper takes as arguments the name of three files: function, argument, and output.
    The files function and argument have the dilled function and argument, respectively.
    The file output is created (or overwritten), with the dilled result of the function call.
    The wrapper created is created/deleted according to the lifetime of the work_queue_executor."""

    name = os.path.join(tmpdir, "fn_as_file")

    with open(name, mode="w") as f:
        f.write(
            """
#!/usr/bin/env python3
import os
import sys
import dill

# import some other packages used by the actual application, e.g.:
import math

(fn, arg, out) = sys.argv[1], sys.argv[2], sys.argv[3]

with open(fn, "rb") as f:
    exec_function = dill.load(f)
with open(arg, "rb") as f:
    exec_args = dill.load(f)

try:
    exec_out = exec_function(*exec_args)
except Exception as e:
    exec_out = e

with open(out, "wb") as f:
    dill.dump(exec_out, f)
""")
    return name


def create_work_queue_task(task_counter, tmpdir, env_wrapper, env_file, fn_wrapper, function, input_args):

    logger.debug("creating task: {}({})".format(function.__name__, ','.join(str(arg) for arg in input_args)))

    args_file = os.path.join(tmpdir, "input_args_{}.p".format(task_counter))
    fn_file   = os.path.join(tmpdir, "function_{}.p".format(task_counter))
    outfile   = os.path.join(tmpdir, "out_{}.p".format(task_counter))

    # Save args to a dilled file.
    with open(args_file, "wb") as wf:
        dill.dump(input_args, wf)

    # Save the executable function in a dilled file.
    with open(fn_file, "wb") as wf:
        dill.dump(function, wf)

    # Base command just invokes python on the function and data.
    command = "python {} {} {} {}".format(
            basename(fn_wrapper),
            basename(fn_file),
            basename(args_file),
            basename(outfile),
            )

    full_cmd = './{w} -e {e} -u "$WORK_QUEUE_SANDBOX"/{e}-env -- {c} 2>&1'.format(w=basename(env_wrapper), e=basename(env_file), c=command)

    task = wq.Task(full_cmd)
    task.specify_tag(str(task_counter))

    task.specify_input_file(env_wrapper, cache=True)
    task.specify_input_file(env_file, cache=True)
    task.specify_input_file(fn_wrapper, cache=True)
    task.specify_input_file(fn_file, cache=False)
    task.specify_input_file(args_file, cache=False)

    # name at manager, name at worker
    task.specify_output_file(outfile, cache=False)

    return task


def application_function(x, y):
    return y/x


class NoResult(Exception):
    def __repr__(self):
        return 'NoResult()'

if __name__ == '__main__':
    env_file = create_python_environment()

    with tempfile.TemporaryDirectory() as tmpdir:
        env_wrapper = shutil.which('python_package_run')
        fn_wrapper = create_function_wrapper(tmpdir)

        q = wq.WorkQueue(port=9123)
        task_counter = 0

        total_tasks = 5

        input_args_for_all_tasks = [ (i, i+1) for i in range(total_tasks) ]
        output_results_for_all_tasks = [None] * total_tasks

        for input_args in input_args_for_all_tasks:
            task = create_work_queue_task(
                    task_counter,
                    tmpdir,
                    env_wrapper, env_file, fn_wrapper,
                    application_function, input_args)
            q.submit(task)
            task_counter += 1

        while not q.empty():
            t = q.wait(5)
            if t:
                if t.result == wq.WORK_QUEUE_RESULT_SUCCESS:
                    if t.return_status != 0:
                        log.warn("task {} had non-zero exit code: {}".format(t.tag, t.return_status))
                    with open(os.path.join(tmpdir, "out_{}.p".format(t.tag)), "rb") as f:
                        output_results_for_all_tasks[int(t.tag)] = dill.load(f)
                else:
                    output_results_for_all_tasks[int(t.tag)] = NoResult()
                    logger.error("task {} failed with: code {}, {}".format(t.tag, task.result, task.result_str))
                    print(t.output)

        print("tasks results:")

        for i in range(0,total_tasks):
            print("application_function({}) = {}".format(
                ','.join(str(arg) for arg in input_args_for_all_tasks[i]),
                output_results_for_all_tasks[i]))

