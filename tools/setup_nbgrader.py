#!/usr/bin/env python3
import argparse
import subprocess
import requests
from requests.exceptions import HTTPError
import os
import stat
import pwd
import re
import random
import string

JUPYTER_ADMIN = 'jupyteradmin'
ADMIN_PWD = 'password'
HOME = "/home2" if os.path.exists("/home2") else "home"

class CourseAlreadyExists(Exception):
    def __init__(self, *args):
        if args:
            self.message = args[0]
        else:
            self.message = None

    def __str__(self):
        if self.message:
            return 'Course {0} already exists'.format(self.message)
        else:
            return 'Course already exists'

class CourseDoesNotExist(Exception):
    def __init__(self, *args):
        if args:
            self.message = args[0]
        else:
            self.message = None

    def __str__(self):
        if self.message:
            return "Course {0} doesn't exists".format(self.message)
        else:
            return "Course doesn't exist"

class MalformedCsvFile(Exception):
    def __init__(self, *args):
        if args:
            self.message = args[0]
        else:
            self.message = None

    def __str__(self):
        if self.message:
            return 'Wrong csv headers : {}'.format(self.message)
        else:
            return 'Wrong csv headers'

class MissingParameter(Exception):
    def __init__(self, *args):
        if args:
            self.message = args[0]
        else:
            self.message = None

    def __str__(self):
        if self.message:
            return 'Missing parameter : {}'.format(self.message)
        else:
            return 'Missing parameter'

def randomString(stringLength=10):
    """Generate a random string of fixed length """
    letters = string.ascii_lowercase
    return ''.join(random.choice(letters) for i in range(stringLength))

srv_root="/srv/nbgrader"
nbgrader_root="/srv/nbgrader/nbgrader"
jupyterhub_root="/srv/nbgrader/jupyterhub"
exchange_root="/srv/nbgrader/exchange"
jh_config_file = os.path.join(jupyterhub_root,'jupyterhub_config.py')



# global nbgrader config
nbgrader_global_config = """from nbgrader.auth import JupyterHubAuthPlugin
c = get_config()
c.Exchange.path_includes_course = True
c.Authenticator.plugin_class = JupyterHubAuthPlugin
"""

# Basic jupyterhub config
# TODO : escape {}
jupyterhub_config = """c = get_config()
c.LocalAuthenticator.create_system_users = True
# c.JupyterHub.bind_url = 'http://127.0.0.1:8000'
c.Authenticator.admin_users = set()
c.JupyterHub.load_groups = {}
c.JupyterHub.services = []
next_port=9999
### End of basic config
########################
"""

# Jupyterhub service
jh_service="""[Unit]
Description=Jupyterhub
After=syslog.target network.target

[Service]
User=root
Environment="PATH=/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=/usr/local/bin/jupyterhub -f /srv/nbgrader/jupyterhub/jupyterhub_config.py
WorkingDirectory=/srv/nbgrader/jupyterhub/
StandardOutput=file:/var/log/jupyterhub.log
StandardError=file:/var/log/jupyterhub-error.log

[Install]
WantedBy=multi-user.target
"""

course_config_base="""c = get_config()
c.CourseDirectory.root = '/{HOME}/{grader}/{course}'
c.CourseDirectory.course_id = '{course}'
"""

def call_api(method, path, datas=None):
    dispatcher = {
        'get': requests.get,
        'post': requests.post,
        'delete': requests.delete,
        'patch': requests.patch
    }
    try:
        func=dispatcher[method]
    except KeyError:
        raise ValueError('invalid method')
    
    api_url = 'http://127.0.0.1:8081/hub/api'
    token = get_token_from_config()
    r = func(os.path.join(api_url,path),
        headers={
                 'Authorization': 'token {}'.format(token),
                },
        json=datas
    )
    r.raise_for_status()
    return r.json()
    

def get_service_repr(course, grader, port, token):
    service = {
          'name': course,
          'url': 'http://127.0.0.1:{}'.format(port),
          'command': [
              'jupyterhub-singleuser',
              f'--group=formgrade-{course}',
              '--debug',
          ],
          'user': grader,
          'cwd': f'{HOME}/{grader}',
          'api_token': f'{token}',
    }  
    return repr(service)

def get_course_config(grader, course):
    return course_config_base.format(HOME=HOME, grader=grader, course=course)

def get_next_port():
    lines = []
    with open(jh_config_file,'r') as cfg:
        for i in cfg:
            if 'next_port' in i:
                [left, port] = i.split(sep='=')
                next_port = int(port) - 1
                lines.append('{}={}\n'.format(left,next_port))
            else:
                lines.append(i)
    with open(jh_config_file,'w') as cfg:
        cfg.writelines(lines)
    return int(port)

def get_token_for_user(user):
    with subprocess.Popen(['jupyterhub','token',user], stdout=subprocess.PIPE, encoding='utf-8', cwd=jupyterhub_root) as proc:
        token=proc.stdout.read().rstrip()
    return token

def get_token_from_config():
    with open(jh_config_file,'r') as cfg:
        lines = cfg.readlines()
    token_line = [l.rstrip() for l in lines if 'admin_token' in l][0]
    token = token_line.split(sep="=")[1]
    token = token.split(sep="'")[1]
    return token

def toggle_nbgrader_component(user, component, enable=True):
    if component not in ['create_assignment','formgrader','assignment_list','course_list']:
        raise KeyError
    usr = pwd.getpwnam(user)
    home = usr.pw_dir
    if enable:
        action = 'enable'
    else:
        action = 'disable'
    command = [ 'sudo','-u',user,
                'jupyter','nbextension', action,
                '--user',"{}/main".format(component)]
    if component != 'create_assignment':
        command.append('--section=tree')  
    subprocess.run(command, env={'HOME':home,'USER':user})  
    if component != 'create_assignment':
        subprocess.run(['sudo','-u',user,
                        'jupyter','serverextension',action,
                        '--user',"nbgrader.server_extensions.{}".format(component)],
                        env={'HOME':home,'USER':user})

### For course management
#########################
def add_jupyter_grader(grader, course):
    append_group = "c.JupyterHub.load_groups.setdefault('formgrade-{}',[]).append('{}')\n".format(course, grader)
    with open(jh_config_file, "a") as f:
        f.write(append_group)

def add_jupyter_students_group(course):
    append_group = "c.JupyterHub.load_groups.setdefault('nbgrader-{}',[])\n".format(course)
    with open(jh_config_file, "a") as f:
        f.write(append_group)

def add_jupyter_admin(username):
    with open(jh_config_file, "a") as f:
        f.write("c.Authenticator.admin_users.add('{}')\n".format(username))

def add_course(args):
    course = args.course_name
    grader_account = "grader-{}".format(course)
    # check if course exists in config
    with open(jh_config_file, 'r') as f :
        if grader_account in f.read():
            raise CourseAlreadyExists('{}'.format(course))
    admin_token = get_token_from_config()
    port = get_next_port()
    print("setting up service with token {} on port {} for course {}".format(admin_token, port, course))
    print("---------------------------------------------------------")
    # Add grader account's password
    try:
        pwd.getpwnam(grader_account)
        # should not get here
    except KeyError:
        os.system('adduser --disabled-password --gecos "" {}'.format(grader_account))
        password = randomString()
        with subprocess.Popen(['passwd',grader_account], stdin=subprocess.PIPE, encoding='utf-8') as proc:
            proc.stdin.write('{}\n'.format(password))
            proc.stdin.write('{}\n'.format(password))

    # need admin rights to add system users
    add_jupyter_admin(grader_account)
    add_jupyter_grader(grader_account, course)
    # empty students group
    add_jupyter_students_group(course)
    
    toggle_nbgrader_component(grader_account, 'formgrader')
    toggle_nbgrader_component(grader_account, 'create_assignment')
    
    with open(jh_config_file,'a') as f:
        # Append service
        service_string = get_service_repr(course,grader_account,port,admin_token)
        f.write("c.JupyterHub.services.append({})\n".format(service_string))
    # create course directory and .jupyter/nbgrader_config.py
    user = pwd.getpwnam(grader_account)
    home = user.pw_dir
    uid = user.pw_uid
    gid = user.pw_gid
    course_dir = os.path.join(home, course)
    os.makedirs(course_dir, exist_ok=True)
    os.chown(course_dir, uid, gid)
    home_jupyter_dir = os.path.join(home, '.jupyter')
    os.makedirs(home_jupyter_dir, exist_ok=True)
    os.chown(home_jupyter_dir, uid, gid)
    with open(os.path.join(home_jupyter_dir,'nbgrader_config.py'),'w') as f:
        f.write(get_course_config(grader_account, course))
    os.system('systemctl restart jupyterhub')

def check_course_exists(course):
    # check if group exists
    try:
        call_api('get', 'services/{}'.format(course))
    except HTTPError as e:
        if e.response.status_code == 404:
            raise CourseDoesNotExist(course)
        # Whatever, we raise
        raise

def add_grader(grader, course):
    # add jupyter user if necessary
    try:
        call_api('post',
                 'users/{}'.format(grader))
    except HTTPError as e:
        if e.response.status_code == 409:
            print('{} already present'.format(grader))
        else:
            # We raise if something went wrong
            raise
        
    # add grader to course
    call_api('post',
                'groups/formgrade-{}/users'.format(course),
                datas={'users':[grader]})

    # ensure teacher is admin
    call_api('patch','users/{}'.format(grader), datas={'admin':True})
    
def add_teacher(args):
    course = args.course_name
    teacher = args.teacher_name
    password = args.password
    check_course_exists(course)

    print('- Adding teacher {} to course : {}'.format(args.teacher_name, args.course_name))
    print('------------------------------------')
    
    # Check for, add and update teacher's password
    try:
        pwd.getpwnam(teacher)
    except KeyError:
        if not password:
            raise MissingParameter('--password')
        os.system('adduser --disabled-password --gecos "" {}'.format(teacher))
        with subprocess.Popen(['passwd',teacher], stdin=subprocess.PIPE, encoding='utf-8') as proc:
            proc.stdin.write('{}\n'.format(password))
            proc.stdin.write('{}\n'.format(password))
    
    # update grader's group
    add_grader(teacher, course)
    
    # setup web UI
    toggle_nbgrader_component(teacher, 'assignment_list')
    toggle_nbgrader_component(teacher, 'course_list')
    # TODO restart needed ?

def add_student(args):
    student = args.sudent_id
    password = args.password
    course = args.course_name
    check_course_exists(course)
    print("- Adding student {} to course : {}".format(student, course))
    print("------------------------------")
    try:
        pwd.getpwnam(student)
        already_exists = True
    except KeyError:
        if not password:
            raise MissingParameter('--password')
        already_exists = False
    
    grader = "grader-{}".format(course)
    token = get_token_from_config()
    # command will create system user if necessary
    command = ['sudo','-u',grader,
            'JUPYTERHUB_USER={}'.format(grader),
            "JUPYTERHUB_API_TOKEN={}".format(token),
            'nbgrader', 'db', 'student', 'add', student,
            '--first-name={}'.format(args.first_name),
            '--last-name={}'.format(args.last_name),
            '--email={}'.format(args.email),
            '--lms-user-id={}'.format(args.lms_user_id)
            ]
    subprocess.run(command,
               cwd='{HOME}/{grader}/{course}'.format(HOME=HOME,grader=grader,course=course),
               env={'HOME': '{HOME}/{grader}'.format(HOME=HOME, grader=grader),
                    'USER':grader})
    
    if not already_exists :
        with subprocess.Popen(['passwd', student], stdin=subprocess.PIPE, encoding='utf-8') as proc:
            proc.stdin.write('{}\n'.format(password))
            proc.stdin.write('{}\n'.format(password))
    toggle_nbgrader_component(student, 'assignment_list')
    # TODO restart hub ?

def import_students(args):
    course = args.course
    check_course_exists(course)
    student_parser = args.student_parser
    print("- Importing students from file {} to course {}".format(args.file, course))
    print("---------------------------------------------")
    with open(args.file) as f:
        first_line = f.readline()
        header_data = [d.rstrip() for d in re.split(',|;', first_line)]
        headers = ['id','first_name','last_name','email','lms_user_id','password']
        if header_data != headers:
            raise MalformedCsvFile(header_data)
        data_line = f.readline()
        while data_line: # TODO empty or malformed lines
            datas = [d.rstrip() for d in re.split(',|;', data_line)]
            ns = student_parser.parse_args([
                datas[0], # id
                course,
                "--first-name={}".format(datas[1]),
                "--last-name={}".format(datas[2]),
                "--email={}".format(datas[3]),
                "--lms-user-id={}".format(datas[4]),
                "--password={}".format(datas[5]),
                ])
            add_student(ns)
            data_line = f.readline()

def install_all(args):
    print('- Installing jupyterhub and nbgrader with service : {}'.format(args.systemd))
    print('----------------------------------------------------')

    os.makedirs(srv_root, exist_ok=True)
    os.chmod(srv_root, os.stat(srv_root).st_mode | 0o444)
    os.makedirs(nbgrader_root, exist_ok=True)
    os.chmod(nbgrader_root, os.stat(nbgrader_root).st_mode | 0o444)
    os.makedirs(jupyterhub_root, exist_ok=True)
    os.chmod(jupyterhub_root, os.stat(jupyterhub_root).st_mode | 0o444)
    with open(jh_config_file, "w") as f:
        f.write(jupyterhub_config)

    curdir = os.getcwd()
    os.chdir(nbgrader_root)
    os.system('git clone https://github.com/Lapin-Blanc/nbgrader .')
    os.system('git checkout create-users-on-demand')
    os.system('pip3 install -U -r requirements.txt -e .')
    os.chdir(curdir)
    
    os.system('jupyter nbextension install --symlink --sys-prefix --py nbgrader --overwrite')

    os.system('jupyter nbextension disable --sys-prefix --py nbgrader')
    os.system('jupyter serverextension disable --sys-prefix --py nbgrader')

    os.system('jupyter nbextension enable --sys-prefix validate_assignment/main --section=notebook')
    os.system('jupyter serverextension enable --sys-prefix nbgrader.server_extensions.validate_assignment')

    if os.path.isdir(exchange_root):
        os.rmdir(exchange_root)

    os.makedirs(exchange_root)
    os.chmod(exchange_root,0o777)
    os.makedirs('/etc/jupyter/', exist_ok=True)
    with open('/etc/jupyter/nbgrader_config.py', "w") as f:
        f.write(nbgrader_global_config)

    # add jupyterhub admin
    os.system('adduser --disabled-password --gecos "" {}'.format(JUPYTER_ADMIN))
    with subprocess.Popen(['passwd',JUPYTER_ADMIN], stdin=subprocess.PIPE, encoding='utf-8') as proc:
        proc.stdin.write('{}\n'.format(ADMIN_PWD))
        proc.stdin.write('{}\n'.format(ADMIN_PWD))
    add_jupyter_admin(JUPYTER_ADMIN)

    token = get_token_for_user(JUPYTER_ADMIN)
    with open(jh_config_file, "a") as f:
        f.write("admin_token='{}'\n".format(token))
    with open("/etc/systemd/system/jupyterhub.service","w") as f:
        f.write(jh_service)
    os.system('systemctl start jupyterhub')
    os.system('systemctl enable jupyterhub')

def import_students_stub(args):
    print("Importing students to course '{}' from file : {}".format(args.course, args.file))

def install_stub(args):
    print('Installing jupyterhub and nbgrader with service : {}'.format(args.systemd))

def add_course_stub(args):
    print('Installing course : {}'.format(args.course_name))

def add_teacher_stub(args):
    print('Adding teacher {} to course : {}'.format(args.teacher_name, args.course_name))

def add_student_stub(args):
    print(args)
    
def main():
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True
    # create the parser for the "install" command
    parser_install = subparsers.add_parser('install', help='install jupyterhub and nbgrader from scratch')
    parser_install.add_argument('-s','--systemd', action='store_true', help='also install startup script')
    parser_install.set_defaults(func=install_all)

    # create the parser for the "add" command
    parser_add = subparsers.add_parser('add', help='add a course, a teacher or a student')

    subparsers_add = parser_add.add_subparsers(dest='element')
    subparsers_add.required = True
    # ADD COURSE
    parser_add_course = subparsers_add.add_parser('course', help='add course help')
    parser_add_course.add_argument('course_name', help='the name of the course to add')
    parser_add_course.set_defaults(func=add_course)

    # ADD TEACHER TO COURSE
    parser_add_teacher = subparsers_add.add_parser('teacher', help='add teacher to existing course')
    parser_add_teacher.add_argument('teacher_name', help='the username of the teacher to add')
    parser_add_teacher.add_argument('course_name', help='the name of the course')
    parser_add_teacher.add_argument('--password', help='required if teacher is created')
    parser_add_teacher.set_defaults(func=add_teacher)

    # ADD STUDENT TO COURSE
    parser_add_student = subparsers_add.add_parser('student', help='add student to existing course')
    parser_add_student.add_argument('sudent_id', help='the id of the student to add')
    parser_add_student.add_argument('course_name', help='the name of the course')
    parser_add_student.add_argument('--first-name', help='the first name of the student to add')
    parser_add_student.add_argument('--last-name', help='the last name of the student to add')
    parser_add_student.add_argument('--email', help='the student\'s email')
    parser_add_student.add_argument('--lms-user-id', help='the lms_id of the student')
    parser_add_student.add_argument('--password', help='required if teacher is created')
    parser_add_student.set_defaults(func=add_student)

    # create the parser for the "import" command
    parser_import = subparsers.add_parser('import', help='import students to course from a file')
    parser_import.add_argument('file', help='file to import')
    parser_import.add_argument('course', help='course to add student to')
    parser_import.set_defaults(func=import_students, student_parser=parser_add_student)

    s = parser.parse_args()
    try:
        s.func(s)
    except (CourseAlreadyExists, CourseDoesNotExist) as e:
        print(e)

if __name__ == "__main__":
    # execute only if run as a script
    main()
