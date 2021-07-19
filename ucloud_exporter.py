import prometheus_client
from prometheus_client import Gauge
from prometheus_client.core import CollectorRegistry
from flask import Response, Flask, current_app
from ucloud.client import Client
from ucloud.core import exc
import time
import threading


class MyThread(threading.Thread):
    def __init__(self, func, args=()):
        super(MyThread, self).__init__()
        self.func = func
        self.args = args

    def run(self):
        self.result = self.func(*self.args)

    def get_result(self):
        try:
            return self.result
        except Exception:
            return None


eip_metric = ["NetworkIn", "NetworkOut", "NetworkOutUsage", "NetworkInUsage"]
vserver_metric = ["UlbVserverHrsp2xx", "UlbVserverHrsp5xx", "CurrentConnections", "UlbVserverHrsp4xx",
                  "UlbVserverHrsp3xx", "NewConnections"]
pg_metric = ["DiskUsage", "CPUUtilization", "MemSize", "ConnectionCount", "QPS", "PingErrorCount", "HAHealthState", "HASyncDelay", "ExpensiveQuery"]
pg_list = [{'resourceid': 'udbha-szyp5njs',  'udb_name': 'easydata', 'udb_type': 'pg'}, {'resourceid': 'udbha-sqwrqvbb',  'udb_name': 'easydata-metrics', 'udb_type': 'pg'}, {'resourceid': 'udbharegion-ituekpk1 ',  'udb_name': 'easydata-hive', 'udb_type': 'mysql'}]

client = Client({
    "region": "cn-bj2",
    "project_id": "org-czaxjv",
    "public_key": "SPkh_vpwCf7xtEIUv4giAXE_oxt8DjSLrEDiwlmS",
    "private_key": "TvmeAmcy_OYr0Ef4cuHzlDpkiU94NU-L47HpESVtgVlnYCrCtaMnVNQ593QCiHVP"
}
)


def get_ulb_vserver():
    dic = {
        "Limit": "500"
    }

    resp = client.invoke("DescribeULB", dic)
    ulb_vserver_list = []
    for ulb_info in resp["DataSet"]:
        if not ulb_info["VServerSet"]:
            continue
        if ulb_info["VServerSet"][0]["ListenType"] != "RequestProxy":
            continue
        vserver = ulb_info["VServerSet"]
        for vserver_id in vserver:
            if "http" not in vserver_id["VServerName"]:
                continue
            ulb_vserver_list.append({"ulb_id": ulb_info["ULBId"], "vserver_name": vserver_id["VServerName"],
                                     "vserver_id": vserver_id["VServerId"], "ulb_name": ulb_info["Name"]})
    return ulb_vserver_list


def get_eip():
    dic = {
        "Limit": 500
    }
    resp = client.invoke("DescribeEIP", dic)
    eip_list = (resp["EIPSet"])
    eip_list = [i for i in eip_list if i["Status"] == "used"]
    return eip_list


def get_metric(metriname, resourceid, resourcetype, zone=None, region=None):
    if not region:
        region = "cn-bj2"
    resourcetype = resourcetype.lower()
    endtime = int(time.time())
    begintime = endtime - 200
    dic = {
        "Region": region,
        "MetricName.0": metriname,
        "ResourceId": resourceid,
        "ResourceType": resourcetype,
        "Zone": zone,
        "BeginTime": str(begintime),
        "EndTime": str(endtime)
    }
    value = get_metric_value(dic)
    return value


def get_metric_value(dic):
    for i in range(3):
        try:
            now_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            resp = client.invoke("GetMetric", dic)
            value = resp['DataSets'][dic["MetricName.0"]][-1]['Value']
            return value
        except Exception as e:
            current_app.logger.warn("get_metric_value err:", e, '---->', i, dic)
    current_app.logger.error("get_metric_value fail three times", dic)
    return -1


app = Flask(__name__)


@app.route("/metrics")
def requests_count():
    REGISTRY = CollectorRegistry(auto_describe=False)
    instances = get_eip()
    for metric in eip_metric:
        thread_list = []
        key = Gauge("ucloud_" + instances[0]["Name"].lower() + "_" + metric.lower(), metric,
                    ['resource_type', 'resource_id', 'bind_resource_name', 'ip'], registry=REGISTRY)
        for instance in instances:
            get_value_thread = MyThread(get_metric, args=(metric, instance["EIPId"], "EIP"))
            thread_list.append({"instance": instance, "thread": get_value_thread})
            get_value_thread.start()
        for threa in thread_list:
            threa["thread"].join()
            value = threa["thread"].get_result()
            if value == None:
                now_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                current_app.logger.warn('eip_none', now_time)
                continue
            instance = threa["instance"]
            key.labels(resource_type="EIP", resource_id=instance["EIPId"],
                       bind_resource_name=instance["Resource"]["ResourceName"],
                       ip=instance["EIPAddr"][0]["IP"]).set(value)
    ulb_list = get_ulb_vserver()
    for metric in vserver_metric:
        thread_list = []
        key = Gauge("ucloud_vserver_" + metric.lower(), metric,
                          ["resource_type", "resource_id", "bind_resource_name", "vserver_name"], registry=REGISTRY)
        for ulb in ulb_list:
            get_value_thread = MyThread(get_metric, args=(metric, ulb["vserver_id"], "ulb-vserver"))
            thread_list.append({"instance": ulb, "thread": get_value_thread})
            get_value_thread.start()
        for threa in thread_list:
            threa["thread"].join()
            value = threa["thread"].get_result()
            if value == None:
                now_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                current_app.logger.warn('ulb_none', now_time)
                continue
            key.labels(resource_type="ulb-vserver", resource_id=threa["instance"]["vserver_id"],
                             bind_resource_name=threa["instance"]["ulb_name"],
                             vserver_name=threa["instance"]["vserver_name"]).set(value)

    for metric in pg_metric:
        thread_list = []
        key = Gauge("ucloud_udb_" + metric.lower(), metric,
                          ["resource_type", "resource_id", "udb_name", "udb_type"], registry=REGISTRY)
        for pg in pg_list:
            get_value_thread = MyThread(get_metric, args=(metric, pg["resourceid"], "udb"))
            thread_list.append({"instance": pg, "thread": get_value_thread})
            get_value_thread.start()
        for threa in thread_list:
            threa["thread"].join()
            value = threa["thread"].get_result()
            if value == None:
                now_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
                current_app.logger.warn('udb_none', now_time)
                continue
            key.labels(resource_type="udb", resource_id=threa["instance"]["resourceid"],
                             udb_name=threa["instance"]["udb_name"], udb_type=threa["instance"]["udb_type"]).set(value)
    return Response(prometheus_client.generate_latest(REGISTRY),
                    mimetype="text/plain")


@app.route('/')
def index():
    return "Hello World"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, processes=True)
