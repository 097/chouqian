from flask import Flask, render_template, request, redirect, url_for, Blueprint
import random
import string
import re
import logging
import logging.handlers
import sys
from datetime import datetime
import os
import pickle
import errno

# 定义数据文件的保存目录
data_directory = '/var/chouqian'


app = Flask(__name__)
# app.config['APPLICATION_ROOT'] = '/cq'
# 创建一个蓝图，指定路径前缀为/cq
cq_blueprint = Blueprint('cq', __name__, url_prefix='/cq')


# 存储抽签活动数据的字典，键为活动链接的随机部分，值为抽签人数
activity_data = {}

# 存储抽签活动状态的字典，键为活动链接，值为活动是否已开始抽签
draw_status = {}

# 存储用户编号的字典，键为用户链接，值为用户编号
user_numbers = {}

# 存储每个活动的队伍分配数据的字典，键为活动链接，值为队伍分配
activity_group_assignments = {}

FILE_ACTIVITY_DATA="activity_data.pkl"
FILE_DRAW_STATUS="draw_status.pkl"
FILE_USER_NUMBERS="user_numbers.pkl"
FILE_ACTIVITY_GROUP_ASSIGNMENTS="activity_group_assignments.pkl"


# Configure logging to send log messages to syslog
syslog_handler = logging.handlers.SysLogHandler(address='/dev/log')
formatter = logging.Formatter('%(name)s: %(levelname)s %(message)s')
syslog_handler.setFormatter(formatter)

logger = logging.getLogger()
logger.addHandler(syslog_handler)
logger.setLevel(logging.INFO)

# Replace all print calls with logging
def print(*args, **kwargs):
    msg = ' '.join(map(str, args))
    logger.info(msg)


@cq_blueprint.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        n = int(request.form['num_of_people'])

        if n % 2 != 0:
            return "抽签人数必须是偶数"

        # 获取用户选择的固定搭档数量
        fixed_count = int(request.form.get('fixed_count_select', '0'))

        print("固定搭档数量:", fixed_count)


        # 生成一个随机的活动ID（6位数字和小写字母组成）
        link_id = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))

        # 获取当前时间并将其格式化为字符串
        current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # 为每位用户生成包含随机字符的抽签链接，并分配用户编号
        user_links = []
        host = request.host
        for i in range(1, n + 1):
            random_chars = ''.join(random.choices(string.ascii_lowercase + string.digits, k=3))
            user_link_id = link_id + random_chars
            user_links.append((f"http://{host}/cq/sd/{user_link_id}", f"用户 {i} 抽签地址"))  # 包含 '/start_draw/' 部分
            # 将用户编号与链接关联起来并存储在内存中
            user_numbers[user_link_id] = i

        # 生成固定搭档的字典，键为用户编号，值为其固定搭档的用户编号
        fixed_partner_assignments = {}
        for i in range(1, fixed_count + 1):
            partner1 = int(request.form.get(f'partner{i}_1', '0'))
            partner2 = int(request.form.get(f'partner{i}_2', '0'))

            # 检查是否选择了有效的固定搭档
            if partner1 == partner2 or partner1 == 0 or partner2 == 0:
                return "固定搭档选择不正确"

            fixed_partner_assignments[partner1] = partner2
            fixed_partner_assignments[partner2] = partner1


        print("存入的固定搭档信息:", fixed_partner_assignments)

        # 将抽签人数、固定搭档数量和固定搭档分配保存到活动数据中
        activity_data[link_id] = {'num_of_people': n, 'fixed_count': fixed_count, 'fixed_partners': fixed_partner_assignments, 'creation_time': current_time}
        save_data_to_file(user_numbers, FILE_USER_NUMBERS)
        save_data_to_file(activity_data, FILE_ACTIVITY_DATA)

        return render_template('links.html', user_links=user_links, activity_id=link_id,
                               activity_results_url=f"http://{host}/cq/ar/{link_id}", creation_time=current_time)

    return render_template('index.html')


@cq_blueprint.route('/recent_activity_links', methods=['GET'])
def recent_activity_links():
    # 获取最近的 20 个活动链接
    recent_activity_links = list(reversed(activity_data.keys()))

    # 创建一个字典，将每个活动链接与创建时间关联
    activity_links_with_time = []
    for link in recent_activity_links:
        creation_time = activity_data.get(link, {}).get('creation_time', "未知时间")
        activity_links_with_time.append((link, creation_time))

    return render_template('recent_activity_links.html', recent_activity_links=activity_links_with_time)


@cq_blueprint.route('/sd/<string:link_id>', methods=['GET', 'POST'])
def start_draw(link_id):
    # 使用 link_id 的前 3 位作为键从 activity_data 中获取 n
    activity_id = link_id[:3]
    activity_info = activity_data.get(activity_id)
    n = activity_info['num_of_people'] if activity_info else None
    creation_time = activity_info['creation_time'] if activity_info else None
    user_number = user_numbers.get(link_id)  # 获取用户编号

    draw_status_id = f"{activity_id}_{user_number}"
    local_draw_status = draw_status.get(draw_status_id, False)

    print("activity id:", activity_id, ", 创建时间:", creation_time)

    if n is None or user_number is None:
        return "无效的链接"

    # 检查是否已经进行了队伍分配
    if activity_id not in activity_group_assignments:
        if request.method == 'POST':
            # 获取固定搭档信息
            fixed_partners = activity_info.get('fixed_partners', {})
            fixed_partners_set = {tuple(sorted(pair)) for pair in fixed_partners.items()}

            print("fixed_partners_set:", fixed_partners_set)

            # 生成随机列表 1-N，其中 N 为抽签人数
            user_numbers_list = list(range(1, n + 1))

            # 从 user_numbers_list 中剔除固定搭档的用户
            for partner1, partner2 in fixed_partners.items():
                if partner1 in user_numbers_list:
                    user_numbers_list.remove(partner1)
                if partner2 in user_numbers_list:
                    user_numbers_list.remove(partner2)
            random.shuffle(user_numbers_list)


            # 按照两两结对的逻辑生成两两结对的集合
            paired_users = []
            while len(user_numbers_list) >= 2:
                pair = set([user_numbers_list.pop(), user_numbers_list.pop()])
                paired_users.append(pair)

            for x in fixed_partners_set:
                paired_users.append(set(x))

            random.shuffle(paired_users)

            # 将 final_users 中的每个 set 转换为对应的组号
            group_assignment = {}
            group_number = 1
            for user_set in paired_users:
                for user_num in user_set:
                    group_assignment[user_num] = group_number
                group_number += 1

            draw_status[draw_status_id] = True
            # 将队伍分配保存在全局字典中，并与活动链接关联
            activity_group_assignments[activity_id] = group_assignment

            save_data_to_file(draw_status, FILE_DRAW_STATUS)
            save_data_to_file(activity_group_assignments, FILE_ACTIVITY_GROUP_ASSIGNMENTS)

            return redirect(url_for('cq.start_draw', link_id=link_id))

        else:
            return render_template('draw.html', link_id=link_id, local_draw_status=local_draw_status, user_number=user_number, activity_id=activity_id, creation_time=creation_time)

    if local_draw_status or request.method == 'POST':
        # 如果已经进行了队伍分配，直接显示抽签结果
        group_assignment = activity_group_assignments.get(activity_id)
        group_number = group_assignment.get(user_number)
        draw_status[draw_status_id] = True

        save_data_to_file(draw_status, FILE_DRAW_STATUS)

        return render_template('draw_result.html', link_id=link_id, group_number=group_number, user_number=user_number, activity_id=activity_id, creation_time=creation_time)
    else:
        return render_template('draw.html', link_id=link_id, local_draw_status=local_draw_status, user_number=user_number, activity_id=activity_id, creation_time=creation_time)



@cq_blueprint.route('/ar/<string:activity_id>', methods=['GET'])
def activity_results(activity_id):
    # 获取指定活动的队伍分配数据
    group_assignment = activity_group_assignments.get(activity_id)
    activity_info = activity_data.get(activity_id, {})
    n = activity_info.get('num_of_people', 0)
    creation_time = activity_info.get('creation_time', '')

    if group_assignment is not None:
        # 初始化两个字典
        user_group_dict = {}
        group_user_dict = {}

        # 遍历队伍分配数据，查找每个用户的组号
        for user_number, group_number in group_assignment.items():
            draw_status_id = f"{activity_id}_{user_number}"
            local_draw_status = draw_status.get(draw_status_id, False)

            # 更新user_group_dict
            if local_draw_status:
                user_group_dict[user_number] = group_number
            else:
                user_group_dict[user_number] = -1

            # 更新group_user_dict
            if group_number not in group_user_dict:
                group_user_dict[group_number] = []

            # 考虑local_draw_status，只有当local_draw_status为True时才添加用户
            if local_draw_status:
                group_user_dict[group_number].append(user_number)

        # 对group_user_dict中的用户列表按用户编号排序
        for group_number, user_list in group_user_dict.items():
            if len(user_list) < 2:
                # 如果用户列表元素个数不足2个，补充编号为999的用户
                user_list.extend([999] * (2 - len(user_list)))
            group_user_dict[group_number] = sorted(user_list)

        # 按键从小到大排序 user_group_dict 和 group_user_dict
        user_group_dict = dict(sorted(user_group_dict.items()))
        group_user_dict = dict(sorted(group_user_dict.items()))    
        return render_template('activity_results.html', user_group_dict=user_group_dict, group_user_dict=group_user_dict, activity_id=activity_id, creation_time=creation_time)
    elif n !=0 :
        # 初始化两个字典
        user_group_dict = {user_number: -1 for user_number in range(1, n + 1)}
        group_user_dict = {group_number: [999, 999] for group_number in range(1, n // 2 + 1)}

        return render_template('activity_results.html', user_group_dict=user_group_dict, group_user_dict=group_user_dict, activity_id=activity_id, creation_time=creation_time)
    else:
        return "无效的活动 ID"


# 创建用于保存数据的函数
def save_data_to_file(data, filename):
    file_path = os.path.join(data_directory, filename)
    with open(file_path, 'wb') as file:
        pickle.dump(data, file)


# 创建用于加载数据的函数
def load_data_from_file(filename, default_data=None):
    file_path = os.path.join(data_directory, filename)
    try:
        with open(file_path, 'rb') as file:
            data = pickle.load(file)
            return data
    except (FileNotFoundError, EOFError):
        return default_data


if __name__ == '__main__':
    # 如果目录不存在，创建它
    try:
        os.makedirs(data_directory)
    except OSError as e:
        if e.errno != errno.EEXIST:
            # 创建目录失败，将数据保存到 /tmp 目录下
            data_directory = '/tmp'


    activity_data = load_data_from_file(FILE_ACTIVITY_DATA, {})
    draw_status = load_data_from_file(FILE_DRAW_STATUS, {})
    user_numbers = load_data_from_file(FILE_USER_NUMBERS, {})
    activity_group_assignments = load_data_from_file(FILE_ACTIVITY_GROUP_ASSIGNMENTS, {})

    app.register_blueprint(cq_blueprint)
    app.run(port=5001)

