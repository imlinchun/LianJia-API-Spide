import base64
import hashlib
import logging
import sys
import threading
import time
from datetime import datetime
from queue import Queue

import requests
from pymongo import MongoClient

# 思路：先获取商圈信息
# 通过商圈信息，多线程去获取成交信息，根据成交信息的数量，把ID及参数放入变量
# 根据这个变量，多线程去最终获取
# 'bizcircle_id': '1100000602', 商圈ID
# 'bizcircle_quanpin': 'chengnanyijia', 商圈全拼
# 'bizcircle_name': '城南宜家', 商圈名
# 'city_id': 510100, 城市Id
# 'city_name': '成都', 城市名称
# 'district_id': '510104', 行政区ID
# 'district_name': '锦江'，行政区名称
# 'condition'：获取api的参数
# subway_line 地铁线
# city_abbr 城市简称


lian_jia = {
    'ua': 'HomeLink7.7.6; Android 7.0',
    'app_id': '20161001_android',
    'app_secret': '7df91ff794c67caee14c3dacd5549b35'}
flag = False  # 退出标志
data_queue = Queue()  # 存放解析数据的queue


# 获取token
def get_token(params):
    data = list(params.items())
    data.sort()
    token = lian_jia['app_secret']
    for entry in data:
        token += '{}={}'.format(*entry)
    token = hashlib.sha1(token.encode()).hexdigest()
    token = '{}:{}'.format(lian_jia['app_id'], token)
    token = base64.b64encode(token.encode()).decode()
    return token


# 解析数据
def parse_data(response):
    as_json = response.json()
    if as_json['errno']:
        # 发生了错误
        raise Exception('请求出错了: ' + as_json['error'])
    else:
        return as_json['data']


# 获取数据
def get_data(url, payload, method='GET', session=None):
    payload['request_ts'] = int(time.time())

    headers = {
        'User-Agent': lian_jia['ua'],
        'Authorization': get_token(payload)
    }
    if session:
        if method == 'GET':
            r = session.get(url, params=payload, headers=headers)
        else:
            r = session.post(url, data=payload, headers=headers)
    else:
        func = requests.get if method == 'GET' else requests.post
        r = func(url, payload, headers=headers)

    return parse_data(r)


# 获取城市信息（某个）
def get_city_info(city_id):
    """
    获取城市信息
    """
    url = 'http://app.api.lianjia.com/config/config/initData'

    payload = {
        'params': '{{"city_id": {}, "mobile_type": "android", "version": "8.0.1"}}'.format(city_id),
        'fields': '{"city_info": "", "city_config_all": ""}'
    }

    data = get_data(url, payload, method='POST')
    city_info = data['city_info']['info'][0]

    for a_city in data['city_config_all']['list']:
        if a_city['city_id'] == city_id:
            # 查找城市名称缩写
            city_info['city_abbr'] = a_city['abbr']
            break

    else:
        logging.error(f'# 抱歉, 链家网暂未收录该城市~')
        sys.exit(1)

    return city_info


# 获取全部城市信息
def get_allcity():
    """
    获取城市信息
    """
    url = 'http://app.api.lianjia.com/config/config/initData'

    payload = {
        'params': '{{"city_id": {}, "mobile_type": "android", "version": "8.0.1"}}'.format(510100),
        'fields': '{"city_info": "", "city_config_all": ""}'
    }

    data = get_data(url, payload, method='POST')
    allcity = []
    for a_city in data['city_config_all']['list']:
        allcity.append({'city_id': a_city['city_id'], 'city_name': a_city['city_name'], 'abbr': a_city['abbr']
                        })

    return allcity


# 获取租房成交数据(城市id,)
def get_rented(city_id, condition):
    url = "https://app.api.lianjia.com/house/rented/search"
    rented = []
    offset = 0
    total_count = get_rented_count(city_id, condition)  # 该商圈的总记录数
    # 总数小于2000，且总数不为0
    while offset < total_count:
        params = {
            'limit_offset': offset,  # 请求数
            'city_id': city_id,
            'limit_count': 20,  # 单次请求数量
            'condition': condition}  # 筛选条件
        data = get_data(url, params)
        # print('         获取成交房源进度：{:.2f}%'.format(offset / total_count * 100))
        for d in data['list']:
            rented.append(d)
        offset += 20
    # print('             总记录数:',total_count,len(rented))
    return rented


# 获取租房成交总数
def get_rented_count(city_id, condition):
    url = "https://app.api.lianjia.com/house/rented/search"
    offset = 0
    params = {
        'limit_offset': offset,  # 请求数
        'city_id': city_id,
        'limit_count': 20,  # 单次请求数量
        'condition': condition,  # 筛选条件

    }
    data = get_data(url, params)
    total_count = data['total_count']  # 总记录数
    return total_count


# 传入城市ID，参数，价格区间，返回该区间的成交数，以及价格拆分为二
# {'rented_count': 2835, 'bpep': {0: 125, 125: 250}}
def get_rented_2000(cityid, condition, bp, ep):
    condition2 = condition + 'brp{}erp{}'.format(bp, ep)
    rented_count2 = get_rented_count(cityid, condition2)
    if rented_count2 <= 2000 and rented_count2 > 0:
        return {'rented_count': rented_count2, 'bpep': {bp: ep}}
    elif rented_count2 > 2000:
        return {'rented_count': rented_count2, 'bpep': {bp: int(ep / 2), int(ep / 2): ep}}
    elif rented_count2 == 0:
        return {'rented_count': rented_count2}


def do_rented_2000(city_id, condition):
    rented_info = []
    price_split = []  # 将价格拆分为可使用的分段
    # 把0，6000带入，返回成交数量信息{'rented_count': 2902, 'bpep': {0: 250, 250: 500}},500至99999为最上限
    rented_info.append(get_rented_2000(city_id, condition, 0, 6000))
    rented_info.append(get_rented_2000(city_id, condition, 6000, 99999))

    for rented in rented_info:
        if rented['rented_count'] > 2000:  # 如果数量大于2000，则进行拆分
            # 遍历超过2000的区间
            for cj in rented['bpep']:
                # print('遍历:',cj,rented['bpep'][cj])
                # 将超过2000的结果，放入rented_info
                rented_info.append(get_rented_2000(city_id, condition, cj, rented['bpep'][cj]))
        elif rented['rented_count'] <= 2000 and rented['rented_count'] != 0:
            # print('该价位区间没有超过2000:',rented['bpep'])
            price_split.append(rented['bpep'])
        else:
            pass  # 区间为0的情况

    return price_split


# 多线程采集，传入原始商圈信息，往队列里写入可直接抓取的商圈信息
class Crawl_thread(threading.Thread):
    '''
    抓取线程类，注意需要继承线程类Thread
    '''

    def __init__(self, thread_id, queue):
        threading.Thread.__init__(self)  # 需要对父类的构造函数进行初始化
        self.thread_id = thread_id
        self.queue = queue  # 任务队列

    def run(self):
        '''
        线程在调用过程中就会调用对应的run方法
        :return:
        '''
        print('启动采集线程：', self.thread_id)
        self.crawl_spider()
        print('退出采集线程：', self.thread_id)

    def crawl_spider(self):
        while True:
            if self.queue.empty():  # 如果队列为空，则跳出
                break
            else:
                bizcircle = self.queue.get()
                total_count = get_rented_count(bizcircle['city_id'], bizcircle['condition'])
                print('     采集线程ID：', self.thread_id, "  {}>{}>{}成交数 {}".format
                (bizcircle['city_name'], bizcircle['district_name'], bizcircle['bizcircle_name'], total_count))
                # 根据返回的总数，分割成不同的ID和参数，放入变量
                if total_count <= 2000 and total_count != 0:
                    data_queue.put(bizcircle)
                elif total_count > 2000:
                    cj = do_rented_2000(bizcircle['city_id'], bizcircle['condition'])
                    bizcircle_tmp = bizcircle.copy()  # 复制一个对象出来，用COPY，不能用=
                    for c in cj:
                        bizcircle_tmp['condition'] = bizcircle['condition'] + 'brp{}erp{}'.format(list(c.keys())[0],
                                                                                                list(c.values())[0])
                        data_queue.put(bizcircle_tmp)
                        #print(bizcircle_tmp)
                        # print('解析完成')



# 多线程处理，传入商圈信息，直接进行最后抓取
class Parser_thread(threading.Thread):
    '''
    解析网页的类，就是对采集结果进行解析，也是多线程方式进行解析
    '''

    def __init__(self, thread_id, queue, db):
        threading.Thread.__init__(self)
        self.thread_id = thread_id
        self.queue = queue
        self.db = db

    def run(self):
        print('启动解析线程：', self.thread_id)
        while not flag:
            try:
                item = self.queue.get(False)  # get参数为false时队列为空，会抛出异常
                if not item:
                    pass
                self.parse_data(item)
                self.queue.task_done()  # 每当发出一次get操作，就会提示是否堵塞
            except Exception as e:
                print('错误1')
        print('退出解析线程：', self.thread_id)

    def parse_data(self, item):
        '''
        解析网页内容的函数
        :param item:
        :return:
        '''
        self.rented = get_rented(item['city_id'], item['condition'])
        for r in self.rented:
            r_copy = r.copy()
            r_copy.update(item)
            r_copy.update({
                '更新时间': datetime.now()})
            self.db.update_one({'house_code': r_copy['house_code']},
                               {'$set': r_copy},
                               upsert=True)

        print('         解析线程ID：', self.thread_id, "  {}>{}>{}>{} 写入完毕。队列剩余链接数：{}".format
        (item['city_name'], item['district_name'], item['bizcircle_name'], item['condition'], self.queue.qsize()))
        # 根据返回的总数，分割成不同的ID和参数，放入变量


def main():
    cityid = 510100  # 设置城市id
    bizcircle_queue = Queue()  # 存放商圈数据到queue
    cityinfo = get_city_info(cityid)
    print('正在获取 {}【{}】行政区域及商圈信息'.format(cityid, cityinfo['city_name']))
    for city in cityinfo['district']:
        # 遍历行政区
        print('{}>{} 商圈数：{}'.format(cityinfo['city_name'], city['district_name'], len(city['bizcircle'])))
        for biz in city['bizcircle']:
            # 遍历商圈
            # 写入城市ID，城市名，区域ID，区域名，商圈信息
            biz_ad = biz
            biz_ad['city_id'] = cityinfo['city_id']
            biz_ad['city_name'] = cityinfo['city_name']
            biz_ad['district_id'] = city['district_id']
            biz_ad['district_name'] = city['district_name']
            biz_ad['condition'] = biz['bizcircle_quanpin'] + '/'
            bizcircle_queue.put(biz_ad)
            print('    商圈名称：{}'.format(biz['bizcircle_name']))
    print('\n' + '    商圈抓取完毕，数量为 {}'.format(bizcircle_queue.qsize()) + '\n')

    conn = MongoClient('127.0.0.1', 27017)
    db = conn.链家网  # 连接mydb数据库，没有则自动创建
    db2 = db[cityinfo['city_name'] + '租房成交信息(多线程/API)']

    # 初始化采集线程
    crawl_threads = []
    for thread_id in range(5):
        # 传入线程ID，
        thread = Crawl_thread(thread_id, bizcircle_queue)  # 启动爬虫线程
        thread.start()  # 启动线程
        crawl_threads.append(thread)

    # 初始化解析线程
    parse_thread = []
    for thread_id in range(30):  #
        thread = Parser_thread(thread_id, data_queue, db2)
        thread.start()  # 启动线程
        parse_thread.append(thread)

    # 等待队列情况，先进行网页的抓取
    while not bizcircle_queue.empty():  # 判断是否为空
        pass  # 不为空，则继续阻塞

    # 等待所有线程结束
    for t in crawl_threads:
        t.join()

    # 等待队列情况，对采集的页面队列中的页面进行解析，等待所有页面解析完成
    while not data_queue.empty():
        pass
    # 通知线程退出
    global flag
    flag = True
    for t in parse_thread:
        t.join()  # 等待所有线程执行到此处再继续往下执行

    print('退出主线程')


if __name__ == '__main__':
    d1 = datetime.now()
    main()
    d2 = datetime.now()
    print('总用时：', d2 - d1)