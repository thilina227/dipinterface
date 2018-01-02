from subprocess import PIPE, run
from io import BytesIO
from flask import Flask, request, send_from_directory, jsonify
import os
import requests
import docker
from werkzeug.utils import secure_filename
from distutils.dir_util import copy_tree, remove_tree

app = Flask(__name__)


@app.route("/")
def root():
    return send_from_directory('ui', 'index.html')


@app.route("/index")
def index():
    return send_from_directory('ui', 'index.html')


@app.route('/<path:path>')
def send_ui(path):
    return send_from_directory('ui', path)


@app.route('/api/backends')
def get_all_apps():
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&verbose=')
    lines = r.text.split('\n')
    i = 0
    backends = []
    while i < len(lines):
        if len(lines[i]) > 0:
            parts = lines[i].split(' ')[1].split(':')
            port = parts[1]
            app_details = get_app_for_port(port)
            backends.append({"appname": app_details.get('appname'),
                             "version": app_details.get('version'),
                             "port": port})
        i = i + 1
    return jsonify(backends)


@app.route('/api/backends/<application>/down')
def down(application):
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&down=')
    return r.text


@app.route('/api/backends/<application>/up')
def up(application):
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&up=')
    return r.text


@app.route('/api/backends/<application>/add')
def add(application):
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&add=')
    return r.text


@app.route('/api/backends/<application>/remove')
def remove(application):
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&remove=')
    return r.text


@app.route('/api/test')
def test():
    container_details = get_port_for_app("myapp", "1.0.0")
    return jsonify(container_details)


@app.route('/api/deploy', methods=['POST'])
def deploy_file():
    if request.method == 'POST':
        file = request.files['file']
        if not request.args.get('version'):
            return "version is empty"
        if not request.args.get('name'):
            return "app name is empty"
        version = request.args.get('version')
        appname = request.args.get('name')
        create_workspace(appname, version)
        copy_assets(appname, version)
        filename = secure_filename(file.filename)
        # saving file to workspace
        file.save(os.path.join('workspace/' + appname + '/' + version, filename))
        # create docker file
        create_docker_file(appname, version)
        # build image
        build_docker_image(appname, version)
        # clean workspace
        clean_assets(appname, version)
        run_container(appname, version)
        add_to_proxy(get_port_for_app(appname, version))
        return 'file deployed successfully'


def add_to_proxy(app_details):
    requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=localhost:' + str(app_details.get('port')) + '&add=')
    requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=localhost:' + str(app_details.get('port')) + '&down=')


def run_container(appname, version):
    client = docker.from_env()
    client.containers.run(appname + ':' + version, 'tomcat/bin/catalina.sh run', detach=True)


def copy_assets(appname, version):
    copy_tree('assets/jre', 'workspace/' + appname + '/' + version + '/jre')
    copy_tree('assets/tomcat', 'workspace/' + appname + '/' + version + '/tomcat')


def clean_assets(appname, version):
    remove_tree('workspace/' + appname + '/' + version + '/tomcat')
    remove_tree('workspace/' + appname + '/' + version + '/jre')


def build_docker_image(appname, version):
    client = docker.from_env()
    client.images.build(path='workspace/' + appname + '/' + version,
                        tag=appname + ':' + version,
                        rm=True)


# deprecated
def build_docker_image_by_command(appname, version):
    build_dir = 'workspace/' + appname + '/' + version
    print("build dir:  " + build_dir)
    build_cmd = ['docker', 'build', build_dir, '-t', appname + ':' + version]
    build_result = run(build_cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)
    print("build output: " + build_result.stdout)
    print("build error: " + build_result.stderr)
    print("built docker image: " + appname + version)


def create_docker_file(appname, version):
    port = get_available_port()
    f = open('workspace/' + appname + '/' + version + '/Dockerfile', 'w')
    f.write('# Tomcat 8 customized\n')
    f.write('#')
    f.write('# VERSION               0.0.1\n')
    f.write('\n')
    f.write('FROM      ubuntu\n')
    f.write('LABEL Description="This image is used to run a customized tomcat server" Version="1.0"\n')
    f.write('ADD tomcat /tomcat\n')
    f.write('ADD jre /jre\n')
    f.write('ADD *.war /tomcat/webapps\n')
    f.write('ENV JAVA_HOME /jre\n')
    f.write('#ENV JAVA_OPTS -Dport.shutdown=8065 -Dport.http=8060\n')
    f.write('RUN sed "s/8080/' + str(port) + '/g" < /tomcat/conf/server.xml > /tmp/server.xml\n')
    f.write('RUN cp /tmp/server.xml /tomcat/conf/server.xml\n')
    f.write('EXPOSE ' + str(port))
    f.close()
    reserve_app_port(appname, version, port)


def get_docker_file(appname, version):
    port = get_available_port()
    docker_file = 'workspace/' + appname + '/' + version + '/Dockerfile \
        # Tomcat 8 customized\n \
        # \n \
        # VERSION               0.0.1\n \
        \n \
        FROM      ubuntu\n \
        LABEL Description="This image is used to run a customized tomcat server" Version="1.0"\n \
        ADD tomcat /tomcat\n \
        ADD jre /jre\n \
        ADD *.war /tomcat/webapps\n \
        ENV JAVA_HOME /jre\n \
        #ENV JAVA_OPTS -Dport.shutdown=8065 -Dport.http=8060\n \
        RUN sed "s/8080/' + str(port) + '/g" < /tomcat/conf/server.xml > /tmp/server.xml\n \
        RUN cp /tmp/server.xml /tomcat/conf/server.xml\n \
        EXPOSE ' + str(port)
    return BytesIO(docker_file.encode('utf-8'))


def reserve_app_port(appname, version, port):
    f = open('conf/app_ports', 'a')
    f.write(str(appname) + ',' + str(version) + ',' + str(port) + '\n')
    f.close()


def release_app_port(appname, version, port):
    content_to_write = ''
    with open('conf/app_ports') as f:
        content = f.read().splitlines()
    i = 0
    while i < len(content):
        parts = content[i].split(',')
        if not parts[0] == appname and parts[1] == version and parts[2] == str(port):
            content_to_write = content[i]
        i = i + 1
    f = open('conf/app_ports', 'w')
    f.write(content_to_write)
    f.close()


def is_port_taken(port):
    with open('conf/app_ports') as f:
        content = f.read().splitlines()
    i = 0
    while i < len(content):
        parts = content[i].split(',')
        if parts[2] == str(port):
            return True
        i = i + 1
    return False


def get_app_for_port(port):
    with open('conf/app_ports') as f:
        content = f.read().splitlines()
    i = 0
    while i < len(content):
        parts = content[i].split(',')
        if parts[2] == str(port):
            return {'appname': parts[0], 'version': parts[1], 'port': port}
        i = i + 1
    print("couldn't find app for the port")
    return {'appname': '', 'version': '', 'port': port}


def get_port_for_app(appname, version):
    client = docker.from_env();
    container_list = client.containers.list(all=True)
    i = 0
    while i < len(container_list):
        container = container_list[i]
        tags = container.image.tags[0]
        tag_parts = tags.split(':')
        img_name = tag_parts[0]
        img_version = tag_parts[1]
        if appname == img_name and version == img_version:
            port = list(container.attrs['NetworkSettings']['Ports'])[0].split('/')[0]
            return {'appname': appname, 'version': version, 'port': int(port)}
        i = i + 1
    return {'appname': appname, 'version': version, 'port': 0}


def get_available_port():
    p = 8300
    while p < 8500:
        p = p + 1
        if not is_port_taken(p):
            return p
    print("Error! Out of ports")


#   creating workspace for the app and version
def create_workspace(appname, version):
    if not os.path.exists('workspace/' + appname):
        os.makedirs('workspace/' + appname)
    if not os.path.exists('workspace/' + appname + '/' + version):
        os.makedirs('workspace/' + appname + '/' + version)


if __name__ == '__main__':
    app.run(debug=True)
