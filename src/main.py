import concurrent.futures
import datetime
import json
import logging
import logging.config
import math
import os
import subprocess
import time

import click
import requests
import urllib3
import yaml
from requests.auth import HTTPBasicAuth
import tarfile


urllib3.disable_warnings()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# global info
api_info = {
    "api_concurrency_limit": 30,
    "apis": [],
    "shell_commands": [],
    "response_status_code_20x": 0,
    "response_status_code_!20x": 0,
    "fail_tasks": {}
}
dnac_config = {}
folder_path = ""
dnac_maglev_path = "/data/tmp/dnac_aura/hc"

_now = datetime.datetime.now()
_today = datetime.date.today()


def make_tarfile(output_filename, source_dir):
    with tarfile.open(output_filename, "w:gz") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))


def get_linux_time_last_days(days):
    _timestamp = []
    _time = []
    _collector_hours_list = [10, 15]

    if _now.hour >= min(_collector_hours_list):
        for one in [i for i in _collector_hours_list if i <= _now.hour]:
            _one = datetime.datetime.combine(_today, datetime.time(one, 0, 0))
            _timestamp.append(str(int(_one.timestamp() * 1000)))

    _day_delta = 1
    while _day_delta <= days:
        _day = _today - datetime.timedelta(days=_day_delta)
        for one in _collector_hours_list:
            _one = datetime.datetime.combine(_day, datetime.time(one, 0, 0))
            _timestamp.append(str(int(_one.timestamp() * 1000)))
        _day_delta += 1
    return _timestamp


def read_yaml(file_path):
    with open(file_path, "r") as f:
        return yaml.safe_load(f)


# get all urls need to capture
urls_list = read_yaml(os.path.abspath(os.path.join(os.path.dirname(__file__), 'urls_list.yaml')))
api_info["timestamp"] = _now.strftime("%Y%m%d_%H%M")


def get_x_auth_token(dnac=dnac_config):
    post_url = f"{dnac['base_url']}dna/system/api/v1/auth/token"
    headers = {'content-type': 'application/json'}

    try:
        r = requests.post(post_url, auth=HTTPBasicAuth(username=dnac["username"], password=dnac["password"]),
                          headers=headers, verify=False)
        r.raise_for_status()
        return r.json()["Token"]
    except requests.exceptions.ConnectionError as e:
        print(f"Error: {e}")


def create_url(basic_path, path="", dnac=dnac_config):
    if path:
        return f"{dnac['base_url']}{basic_path}/{path}"
    else:
        return f"{dnac['base_url']}{basic_path}"


def new_request_basic(url, token, headers={}):
    _success = True
    headers.update({"X-Auth-Token": token})
    start_time = time.perf_counter()
    response = requests.get(create_url(url["url"]), headers=headers, verify=False)
    _data = json.loads(response.text)
    elapsed_time_1 = time.perf_counter() - start_time

    with open(f'output/{folder_path}/{url["name"]}.json', 'w') as outfile:
        json.dump(_data, outfile)

    if 200 <= response.status_code < 211:
        api_info["response_status_code_20x"] += 1
        if url["name"] in api_info["fail_tasks"].keys():
            api_info["fail_tasks"].pop(url["name"])
        logging.info(f'{url["name"]} success')
        # print("!", end='')
    else:
        api_info["response_status_code_!20x"] += 1
        api_info["fail_tasks"][url["name"]] = {
            "url": url,
            "task": "request_basic"
        }
        logging.error(f'{url["name"]} has {response.status_code}')
        # print(".", end='')
        _success = False

    api_info["apis"].append(
        {"name": url["name"], "url": url["url"], "method": "get", "status_code": response.status_code,
         "elapsed": f'{elapsed_time_1:0.2f}s', "size(byte)": len(response.text), "success": _success})
    return {
        "name": url["name"],
        "v": _data
    }


def get_site_global_id(data):
    if data:
        if data[0].get("id"):
            return data[0]["id"]


def new_task_1_urls():
    _task_1_url = urls_list["basic"].copy()
    for url in urls_list["loop"]:
        _timestamp = get_linux_time_last_days(1)
        for timestamp in _timestamp:
            _task_1_url.append({
                "url": f'{url["url"]}?timestamp={timestamp}',
                "name": f'{url["name"]}_{timestamp}'
            })
    _task_1_url.append(urls_list["devices"]["site"])

    # remove not needed items
    _data = []
    for i in _task_1_url:
        if "need" in i.keys():
            if not i.get("need"):
                continue
        _data.append(i)

    return _data


@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    if not ctx.invoked_subcommand:
        ctx.invoke(run)


@click.command()
@click.option("--name", prompt="please input the DNAC client name ", help="Client_name_location of DNAC.")
@click.option("--url", default="localhost", prompt="please input the IP of DNAC", help="host or IP of DNAC.")
@click.option("--username", default="admin", prompt="please input the username for access DNAC GUI", help="username of DNAC.")
@click.password_option(prompt="please input the password for access DNAC GUI", help="password of DNAC.", confirmation_prompt=False)
# @click.option('--version', default="2.2.1", type=click.Choice(["1.3.3", "2.1.1", "2.1.2", "2.2.1", "2.2.2.3"]), prompt="please select the DNAC version", help="Verison of DNAC")
# @click.option('--ssl/--no-ssl', default=False, prompt="please select to enable SSL verify", help="Enable SSL verify or not")
def init(name, url, username, password, version="", ssl=False):
    """ Step one: interactive create the config.yml file, please run : ./dnac-hc init, then the config.yml file will be created automatically in the folder
    """
    _config = {
            "name": name,
            "base_url": f'https://{url}/',
            "username": username,
            "password": password,
            "version": version,
            "verify": ssl
        }
    with open(f'config.yml', "w") as file:
        yaml.dump(_config, file)
        print("config.yml file created successfully, next step run command: ./dnac-hc")
    return


def new_task_1_run(urls, token):
    loop_index = 1
    offset = 0
    loop_no = math.ceil(len(urls) / api_info["api_concurrency_limit"])

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        while loop_index <= loop_no:
            _time = time.perf_counter()
            for url in urls[slice(offset, loop_index * api_info["api_concurrency_limit"])]:
                futures.append(executor.submit(new_request_basic, url=url, token=token))

            for future in concurrent.futures.as_completed(futures):
                _v = future.result()
                if "get_site_global" in _v.get("name"):
                    global_id = get_site_global_id(_v.get("v").get("response"))
                if "get_devices_count" in _v.get("name"):
                    _devices_count = _v.get("v").get("response")

            offset += api_info["api_concurrency_limit"]
            print(f'Now run index/total loops {loop_index}/{loop_no}, it took: {time.perf_counter() - _time}')
            logging.info("Finished this task........, wait 60 seconds...")
            if loop_index < loop_no:
                time.sleep(60)
            loop_index += 1

        if global_id:
            _url = urls_list["devices"]["site_membership_g"]
            futures.append(executor.submit(new_request_basic, url={"url": f'{_url["url"]}/{global_id}', "name": _url["name"]}, token=token))
            for i in concurrent.futures.as_completed(futures):
                pass

        if _devices_count > 0:
            _url = urls_list["devices"]["device_list"]
            if _url.get("need"):
                for i in range(math.ceil(int(_devices_count)/500)):
                    futures.append(executor.submit(new_request_basic, url={"url": f'{_url["url"]}?offset={str(i*500+1)}&limit=500', "name": f'{_url["name"]}_{str(i)}'}, token=token))
                for i in concurrent.futures.as_completed(futures):
                    pass

            _url = urls_list["devices"]["device_health"]
            if _url.get("need"):
                for i in range(math.ceil(int(_devices_count)/1000)):
                    futures.append(executor.submit(new_request_basic, url={"url": f'{_url["url"]}?offset={str(i*1000+1)}&limit=1000', "name": f'{_url["name"]}_{str(i)}'}, token=token))
                for i in concurrent.futures.as_completed(futures):
                    pass


def run_shell(cmd_list):
    _cmd = []
    for cmd in cmd_list:
        _output = subprocess.run(cmd, capture_output=True, shell=True)
        if _output.returncode == 0:
            with open(f'output/{folder_path}/shell_{cmd}.txt', "w") as file:
                file.write(_output.stdout.decode("utf-8"))

        api_info["shell_commands"].append({
            "command": cmd,
            "return_code": _output.returncode
        })
        _cmd.append(cmd)
    logging.info(f'Total shell command -{len(_cmd)} was runs..')


@click.command()
def run():
    """ Step two: run this command to capture all commands : ./dnac-hc
    """
    # get all dnac info from config.yml  file
    global folder_path, dnac_config
    if not os.path.isfile('config.yml'):
        print("config.yml not exist, please create it first using command: ./dnac-hc init")
        return
    else:
        # print("file exist")
        with open('config.yml') as f:
            dnac_config.update(yaml.load(f, Loader=yaml.FullLoader))

    folder_path = dnac_config.get("name") + "_" + _now.strftime("%Y%m%d-%H%M%S")

    if not os.path.exists(f'output/{folder_path}'):
        os.makedirs(f'output/{folder_path}')

    _csv = {}
    logging.info(f"Start collect information of this DNAC: {dnac_config['name']}")
    start_time = time.perf_counter()
    token = get_x_auth_token(dnac_config)

    # task
    _url = new_task_1_urls()
    new_task_1_run(_url, token)
    run_shell([i.get("command", "") for i in urls_list.get("shell", "")])

    end_time = time.perf_counter()
    dnac_config.pop("username")
    dnac_config.pop("password")
    _dnac_info = {"config": dnac_config, "urls_list": urls_list}

    with open(f'output/{folder_path}/' + 'DNAC info.json', 'w') as outfile:
        json.dump(_dnac_info, outfile, indent=4)
    with open(f'output/{folder_path}/' + 'API info.json', 'w') as outfile:
        json.dump(api_info, outfile, indent=4)
    print(f"Elapsed total time: {end_time - start_time} seconds.")
    print(f'Total APIs count successfully/failures：{api_info.get("response_status_code_20x")}/{api_info.get("response_status_code_!20x")}')

    # make tar file
    make_tarfile(f'output/dnac-hc_{folder_path}.tar.gz', f'output/{folder_path}')


if __name__ == "__main__":
    cli.add_command(init)
    cli.add_command(run)
    cli()




