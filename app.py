from subprocess import PIPE, run
from io import BytesIO
from flask import Flask, request, send_from_directory, jsonify, redirect, url_for
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


@app.route('/ui/deploy', methods=['POST'])
def deploy_file_from_ui():
    if request.method == 'POST':
        file = request.files['file']
        if not request.form.get('version'):
            return "version is empty"
        if not request.form.get('appname'):
            return "app package name is empty"
        version = request.form.get('version')
        appname = request.form.get('appname')
        create_workspace(appname, version)
        copy_assets(appname, version)
        filename = secure_filename(file.filename)
        # saving file to workspace
        file.save(os.path.join('workspace/' + appname + '/' + version, filename))
        # create docker file
        create_docker_file(appname, version)
        # get an available port
        port = get_available_port()
        # reserve port
        reserve_app_port(appname, version, port)
        # build image
        build_docker_image(appname, version)
        # clean workspace
        clean_assets(appname, version)
        run_container(appname, version, port)
        add_to_proxy(port)
        return redirect(url_for('index'))


@app.route('/api/backends')
def get_all_apps():
    apps = []
    client = docker.from_env()
    images = client.images.list(all=True)
    j = 0
    while j < len(images):
        image = images[j]
        if len(image.tags):
            tag = image.tags[0]
            tag_parts = tag.split(':')
            appname = tag_parts[0]
            version = tag_parts[1]
            if appname != 'ubuntu':
                running = is_running(tag_parts[0], tag_parts[1])
                connected = is_connected(tag_parts[0], tag_parts[1])
                apps.append({"appname": appname,
                             "version": version,
                             "port": get_port_for_running_app(appname, version)['port'],
                             "isConnected": connected,
                             "isRunning": running})
        j = j + 1
    return jsonify(apps)


def is_connected(appname, version):
    proxy_backends = get_all_proxy_connections()
    for backend in proxy_backends:
        if backend['appname'] == appname and backend['version'] == version:
            return backend['isConnected']
    return False


def does_connection_exists(port):
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&verbose=')
    lines = r.text.split('\n')
    i = 0
    while i < len(lines):
        if len(lines[i]) > 0:
            line_parts = lines[i].split(' ')
            backend_parts = line_parts[1].split(':')
            if port == backend_parts[1]:
                return True
        i = i + 1
    return False


# fetch backends from nginx proxy
def get_all_proxy_connections():
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&verbose=')
    lines = r.text.split('\n')
    i = 0
    backends = []
    while i < len(lines):
        if len(lines[i]) > 0:
            line_parts = lines[i].split(' ')
            backend_parts = line_parts[1].split(':')
            port = backend_parts[1]
            status = line_parts[len(line_parts) - 1]
            if status == "down;":
                connected = False
            else:
                connected = True
            app_details = get_app_for_port(port)
            backends.append({"appname": app_details.get('appname'),
                             "version": app_details.get('version'),
                             "port": port,
                             "isConnected": connected})
        i = i + 1
    return backends


@app.route('/api/backends/<application>/down')
def down(application):
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&down=')
    return r.text


@app.route('/api/backends/<application>/up')
def up(application):
    port = application.split(':')[1]
    if does_connection_exists(port):
        r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&up=')
    else:
        r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&add=')
    return r.text


@app.route('/api/backends/<application>/add')
def add(application):
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&add=')
    return r.text


@app.route('/api/backends/<application>/remove')
def remove(application):
    appname = request.args.get('appname')
    version = request.args.get('version')
    terminate_container(appname, version)
    r = requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=' + application + '&remove=')
    terminate_image(appname, version)
    release_app_port(appname, version, application.split(':')[1])
    return r.text


@app.route('/api/backends/stop')
def stop():
    appname = request.args.get('appname')
    version = request.args.get('version')
    terminate_container(appname, version)
    return "container stopped and removed"


@app.route('/api/backends/start')
def start():
    appname = request.args.get('appname')
    version = request.args.get('version')
    port = get_port_for_stopped_app(appname, version)['port']
    run_container(appname, version, port)
    return "container started"


@app.route('/api/test')
def test():
    var = is_running("myapp", "1.0.0")
    return "test"


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
        # get an available port
        port = get_available_port()
        # reserve port
        reserve_app_port(appname, version, port)
        # build image
        build_docker_image(appname, version)
        # clean workspace
        clean_assets(appname, version)
        run_container(appname, version, port)
        add_to_proxy(port)
        return 'file deployed successfully'


def add_to_proxy(port):
    requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=127.0.0.1:' + str(port) + '&add=')
    requests.get('http://127.0.0.1:10000/dynamic?upstream=backends&server=127.0.0.1:' + str(port) + '&down=')


def run_container(appname, version, port):
    client = docker.from_env()
    client.containers.run(appname + ':' + version, 'tomcat/bin/catalina.sh run', detach=True,
                          ports={'8080/tcp': port})


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


def terminate_container(appname, version):
    client = docker.from_env()
    containers = client.containers.list(all=True)
    i = 0
    while i < len(containers):
        tag = containers[i].image.tags[0]
        tag_parts = tag.split(":")
        if appname == tag_parts[0] and version == tag_parts[1]:
            # stop container
            containers[i].kill()
            containers[i].remove()
            print("terminated " + appname + ":" + version)
            break
        i = i + 1


def terminate_image(appname, version):
    client = docker.from_env()
    images = client.images.list(all=True)
    i = 0
    while i < len(images):
        if len(images[i].tags) > 0:
            tag = images[i].tags[0]
            tag_parts = tag.split(":")
            if appname == tag_parts[0] and version == tag_parts[1]:
                # remove image
                client.images.remove(image=appname + ':' + version, force=True)
                print("terminated image " + appname + ":" + version)
                break
        i = i + 1


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
    # port = get_available_port()
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
#    f.write('#RUN sed "s/8080/' + str(port) + '/g" < /tomcat/conf/server.xml > /tmp/server.xml\n')
#    f.write('#RUN cp /tmp/server.xml /tomcat/conf/server.xml\n')
    f.write('EXPOSE 8080')
    f.close()


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


def get_port_for_running_app(appname, version):
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
            port = container.attrs['NetworkSettings']['Ports']['8080/tcp'][0]['HostPort']
            return {'appname': appname, 'version': version, 'port': int(port)}
        i = i + 1
    return {'appname': appname, 'version': version, 'port': 0}


def get_port_for_stopped_app(appname, version):
    with open('conf/app_ports') as f:
        content = f.read().splitlines()
    i = 0
    while i < len(content):
        parts = content[i].split(',')
        if parts[0] == appname and parts[1] == version:
            return {'appname': parts[0], 'version': parts[1], 'port': parts[2]}
        i = i + 1
    print("couldn't find app for the port")
    return {'appname': '', 'version': '', 'port': 0}


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


def is_running(appname, version):
    client = docker.from_env()
    containers = client.containers.list(all=True)
    i = 0
    while i < len(containers):
        tag = containers[i].image.tags[0]
        tag_parts = tag.split(':')
        if appname == tag_parts[0] and version == tag_parts[1]:
            return True
        i = i + 1
    return False


if __name__ == '__main__':
    app.run(debug=True)
