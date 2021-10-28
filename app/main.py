import datetime
import time
import json
import logging
import logging.config
import math
import os
import calendar

import requests
import urllib3
import yaml
from requests.auth import HTTPBasicAuth
import concurrent.futures


urllib3.disable_warnings()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# global info
api_info = {
    "api_concurrency_limit": 30,
    "apis": [],
    "response_status_code_20x": 0,
    "response_status_code_!20x": 0,
    "deviceFamily": {},
    "fail_tasks": {}
}
dnac_info = {}
deviceFamily = {
    "familyList": ("Switches and Hubs", "Wireless Controller", "Routers", "Unified AP"),
    "devices": [],
    "top_nodes": {},
    "top_links": [],
    "site_top": {},
    "sda_role": {},
    "lisp": {},
    "csv": [],
    "design": [],
    "inventory": [],
    "health": {},
    "dnac": []
}

_now = datetime.datetime.now()
_today = datetime.date.today()


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


def get_commands_per_type(data):
    _commands = data.pop("commands")
    _cmds_per_type = {}
    for one_type, group_name_list in data.items():
        _cmds = []
        for one_group in group_name_list:
            # _cmds.extend(_commands.get(one_group))
            for one in _commands[one_group]:
                if one:
                    _cmds.append(one)
        if _cmds:
            _cmds_per_type[one_type] = list(set(_cmds))

    return _cmds_per_type


def read_yaml(file_path):
    with open(file_path, "r") as f:
        return yaml.safe_load(f)


# get cmds for type: edge, border etc.
commands_list = get_commands_per_type(read_yaml("commands_list.yaml"))
urls_list = read_yaml("urls_list.yaml")
config = {}


# get OS ENV or default value
def get_dnac_config():
    _data = dict(
        name=os.getenv('dnac_name', "DNAC"),
        base_url=os.getenv('dnac_base_url', "127.0.0.1"),
        username=os.getenv('dnac_username', "maglev"),
        password=os.getenv('dnac_password', "cisco"),
        version=os.getenv('dnac_version', "1.3.3"),
        verify=(os.getenv('dnac_verify', "False") == 'True')
    )
    return _data


config["dnac"] = get_dnac_config()
folder_path = f'{config["dnac"].get("name")}_{_now.strftime("%Y_%m_%d")}'

if not os.path.exists(f'output/{folder_path}'):
    os.makedirs(f'output/{folder_path}')
api_info["timestamp"] = _now.strftime("%Y%m%d_%H%M")

deviceFamily["dnac"].extend([{
    "name": "collect_time",
    "value": f'{_now.strftime("%A, %B %d, %Y %I:%M%p")}, as: {1000 * (calendar.timegm(_today.timetuple()))}'
}, {
    "name": "DNAC_name",
    "value": config["dnac"].get("name")
}])


def get_x_auth_token(dnac=config["dnac"]):
    post_url = f"{dnac['base_url']}dna/system/api/v1/auth/token"
    headers = {'content-type': 'application/json'}

    try:
        r = requests.post(post_url, auth=HTTPBasicAuth(username=dnac["username"], password=dnac["password"]),
                          headers=headers, verify=False)
        r.raise_for_status()
        return r.json()["Token"]
    except requests.exceptions.ConnectionError as e:
        print(f"Error: {e}")


def create_url(basic_path, path="", dnac=config["dnac"]):
    if path:
        return f"{dnac['base_url']}{basic_path}/{path}"
    else:
        return f"{dnac['base_url']}{basic_path}"


def new_request_basic(url, headers={}):
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
        print("!", end='')
    else:
        api_info["response_status_code_!20x"] += 1
        api_info["fail_tasks"][url["name"]] = {
            "url": url,
            "task": "request_basic"
        }
        logging.error(f'{url["name"]} has {response.status_code}')
        print(".", end='')
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


def new_task_1_run(urls):
    loop_index = 1
    offset = 0
    loop_no = math.ceil(len(urls) / api_info["api_concurrency_limit"])

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futures = []
        while loop_index <= loop_no:
            _time = time.perf_counter()
            for url in urls[slice(offset, loop_index * api_info["api_concurrency_limit"])]:
                futures.append(executor.submit(new_request_basic, url=url))

            for future in concurrent.futures.as_completed(futures):
                _v = future.result()
                if "get_site_global" in _v.get("name"):
                    global_id = get_site_global_id(_v.get("v").get("response"))
                if "get_devices_count" in _v.get("name"):
                    _devices_count = _v.get("v").get("response")

            print(f'Time need : {time.perf_counter() - _time}')
            logging.info(f"Finished in Task 1 for Loop NO.: {loop_index}")
            offset += api_info["api_concurrency_limit"]
            print(f'index is {loop_index}, loop is {loop_no}')
            if loop_index < loop_no:
                time.sleep(60)
            loop_index += 1

        if global_id:
            _url = urls_list["devices"]["site_membership_g"]
            futures.append(executor.submit(new_request_basic, url={"url": f'{_url["url"]}/{global_id}', "name": _url["name"]}))
            for i in concurrent.futures.as_completed(futures):
                pass

        if _devices_count > 0:
            _url = urls_list["devices"]["devices"]
            for i in range(math.ceil(int(_devices_count)/500)):
                futures.append(executor.submit(new_request_basic, url={"url": f'{_url["url"]}?offset={str(i*500+1)}&limit=500', "name": f'{_url["name"]}_{str(i)}'}))
            for i in concurrent.futures.as_completed(futures):
                pass


def new_main():
    global token
    _csv = {}
    logging.info(f"Start collect information of this DNAC: {config['dnac']['name']}")
    start_time = time.perf_counter()
    token = get_x_auth_token()

    # task
    _url = new_task_1_urls()
    new_task_1_run(_url)

    end_time = time.perf_counter()
    dnac_info.update({"config": config, "commands_list": commands_list, "urls_list": urls_list})

    with open(f'output/{folder_path}/' + 'DNAC info.json', 'w') as outfile:
        json.dump(dnac_info, outfile, indent=4)
    with open(f'output/{folder_path}/' + 'API info.json', 'w') as outfile:
        json.dump(api_info, outfile, indent=4)
    print(f"Elapsed run total time: {end_time - start_time} seconds.")
    print(f'Total APIs status code 20x/其他：{api_info.get("response_status_code_20x")}/{api_info.get("response_status_code_!20x")}')


if __name__ == "__main__":
    new_main()
