#!/usr/bin/env python3
import argparse
import subprocess
import os
import stat
import pwd


JUPYTER_ADMIN = 'jupyteradmin'
ADMIN_PWD = 'password'

class CourseAlreadyExists(Exception):
    def __init__(self, message):
        # Call the base class constructor with the parameters it needs
        super(CourseAlreadyExists, self).__init__(message)


deps = [
'apt update',
'apt upgrade -o Dpkg::Options::="--force-confold" --force-yes -y',
'apt install -y npm',
'npm install -g configurable-http-proxy',
'apt install -y python3-pip',
'pip3 install -U jupyter',
'pip3 install -U jupyterhub',
]

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

# Base service
def get_service_repr(course, grader, port, token):
  service = {
          'name': course,
          'url': 'http://127.0.0.1:{}'.format(port),
          'command': [
              'jupyterhub-singleuser',
              '--group=formgrade-{}'.format(course),
              '--debug',
          ],
          'user': grader,
          'cwd': '/home/{}'.format(grader),
          'api_token': '{}'.format(token),
  }  
  return repr(service)

course_config_base="""c = get_config()
c.CourseDirectory.root = '/home/{grader}/{course}'
c.CourseDirectory.course_id = '{course}'
"""

def get_course_config(grader, course):
    return course_config_base.format(grader=grader, course=course)

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
    with open(jh_config_file,'w+') as cfg:
        cfg.writelines(lines)
    return int(port)

def add_system_user(username, password):
    os.system('adduser --disabled-password --gecos "" {}'.format(username))
    with subprocess.Popen(['passwd',username], stdin=subprocess.PIPE, encoding='utf-8') as proc:
        proc.stdin.write('{}\n'.format(password))
        proc.stdin.write('{}\n'.format(password))

def add_jupyter_admin(username):
    with open(jh_config_file, "a") as f:
        f.write("c.Authenticator.admin_users.add('{}')\n".format(username))

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

def add_jupyter_grader(grader, course):
    append_group = "c.JupyterHub.load_groups.setdefault('formgrade-{}',[]).append('{}')\n".format(course,grader)
    with open(jh_config_file, "a") as f:
        f.write(append_group)

def add_jupyter_students_group(course):
    append_group = "c.JupyterHub.load_groups.setdefault('nbgrader-{}',[])\n".format(course)
    with open(jh_config_file, "a") as f:
        f.write(append_group)

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

def add_course(args):
    course = args.course_name
    admin_token = get_token_from_config()
    port = get_next_port()
    grader_account = "grader-{}".format(course)
    # TODO course already exists
    print("setting up service with token {} on port {} for course {}".format(admin_token, port, course))
    # TODO raise exception if course already exists ?
    # at least, don't add id as a service
    try:
        pwd.getpwnam(grader_account)
    except KeyError:
        # TODO fix password
        add_system_user(grader_account, grader_account)
    # need admin rights to add system users
    add_jupyter_admin(grader_account)
    add_jupyter_grader(grader_account, course)
    
    toggle_nbgrader_component(grader_account, 'formgrader')
    toggle_nbgrader_component(grader_account, 'create_assignment')
    
    add_jupyter_students_group(course)
    with open(jh_config_file,'a') as f:
        service_string = get_service_repr(course,grader_account,port,admin_token)
        f.write("c.JupyterHub.services.append({})\n".format(service_string))
    user = pwd.getpwnam(grader_account)
    home = user.pw_dir
    uid = user.pw_uid
    gid = user.pw_gid
    course_dir = os.path.join(home,course)
    os.makedirs(course_dir, exist_ok=True)
    os.chown(course_dir, uid, gid)
    home_jupyter_dir = os.path.join(home,'.jupyter')
    os.makedirs(home_jupyter_dir, exist_ok=True)
    os.chown(home_jupyter_dir, uid, gid)
    # TODO find w+
    with open(os.path.join(home_jupyter_dir,'nbgrader_config.py'),'w') as f:
        f.write(get_course_config(grader_account, course))
    # TODO reload if service file
   
def add_teacher(args):
    print('Adding teacher {} to course : {}'.format(args.teacher_name, args.course_name))
    course = args.course_name
    teacher = args.teacher_name
    try:
        pwd.getpwnam(teacher)
    except KeyError:
        # TODO fix password
        add_system_user(teacher, teacher)
    # need admin rights to list courses
    # TODO check if already in group
    add_jupyter_admin(teacher)
    add_jupyter_grader(teacher, course)
    
    toggle_nbgrader_component(teacher, 'assignment_list')
    toggle_nbgrader_component(teacher, 'course_list')
    # TODO reload if service file




def install_all(args):
    print('Installing jupyterhub and nbgrader with service : {}'.format(args.systemd))
    for dep in deps:
        os.system(dep)
    os.makedirs(srv_root, exist_ok=True)
    os.chmod(srv_root, os.stat(srv_root).st_mode | 0o444)
    os.makedirs(nbgrader_root, exist_ok=True)
    os.chmod(nbgrader_root, os.stat(nbgrader_root).st_mode | 0o444)
    os.makedirs(jupyterhub_root, exist_ok=True)
    os.chmod(jupyterhub_root, os.stat(jupyterhub_root).st_mode | 0o444)
    with open(jh_config_file, "w+") as f:
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
    add_system_user(JUPYTER_ADMIN,ADMIN_PWD)
    add_jupyter_admin(JUPYTER_ADMIN)
    token = get_token_for_user(JUPYTER_ADMIN)
    with open(jh_config_file, "a") as f:
        f.write("admin_token='{}'\n".format(token))
    with open("/etc/systemd/system/jupyterhub.service","w") as f:
        f.write(jh_service)
    os.system('systemctl start jupyterhub')
    os.system('systemctl enable jupyterhub')


parser = argparse.ArgumentParser()

def install_stub(args):
    print('Installing jupyterhub and nbgrader with service : {}'.format(args.systemd))


def add_course_stub(args):
    print('Installing course : {}'.format(args.course_name))

def add_teacher_stub(args):
    print('Adding teacher {} to course : {}'.format(args.teacher_name, args.course_name))

def main():
    subparsers = parser.add_subparsers(dest='command')
    subparsers.required = True
    # create the parser for the "install" command
    parser_install = subparsers.add_parser('install', help='install jupyterhub and nbgrader from scratch')
    parser_install.add_argument('-s','--systemd', action='store_true', help='also install startup script')
    parser_install.set_defaults(func=install_all)

    # create the parser for the "add" command
    parser_add = subparsers.add_parser('add', help='add a course or a teacher')

    subparsers_add = parser_add.add_subparsers(dest='element')
    subparsers_add.required = True
    # ADD COURSE
    parser_add_course = subparsers_add.add_parser('course', help='add course help')
    parser_add_course.add_argument('course_name', help='the name of the course to add')
    parser_add_course.set_defaults(func=add_course)

    # ADD TEACHER TO COURSE
    parser_add_teacher = subparsers_add.add_parser('teacher', help='add teacher to existing course')
    parser_add_teacher.add_argument('teacher_name', help='the name of the teacher to add')
    parser_add_teacher.add_argument('course_name', help='the name of the course')
    parser_add_teacher.set_defaults(func=add_teacher)

    s = parser.parse_args()
    s.func(s)

if __name__ == "__main__":
    # execute only if run as a script
    main()
