from flask import Flask, render_template, request, redirect, url_for, Blueprint
import random
import string
import re
import logging
import logging.handlers
import sys


app = Flask(__name__)
# app.config['APPLICATION_ROOT'] = '/cq'
# 创建一个蓝图，指定路径前缀为/cq
cq_blueprint = Blueprint('cq', __name__, url_prefix='/cq')


# 存储抽签活动数据的字典，键为活动链接的随机部分，值为抽签人数
activity_data = {}

# 存储用户分组数据的字典，键为活动链接，值为用户所在的组号
user_data = {}

# 存储抽签活动状态的字典，键为活动链接，值为活动是否已开始抽签
draw_status = {}

# 存储用户编号的字典，键为用户链接，值为用户编号
user_numbers = {}

# 存储每个活动的队伍分配数据的字典，键为活动链接，值为队伍分配
activity_group_assignments = {}


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
        activity_data[link_id] = {'num_of_people': n, 'fixed_count': fixed_count, 'fixed_partners': fixed_partner_assignments}

        return render_template('links.html', user_links=user_links, activity_id=link_id,
                               activity_results_url=f"http://{host}/cq/ar/{link_id}")

    return render_template('index.html')


@cq_blueprint.route('/recent_activity_links', methods=['GET'])
def recent_activity_links():
    # 获取最近的 20 个活动链接
    recent_activity_links = list(reversed(activity_data.keys()))

    return render_template('recent_activity_links.html', recent_activity_links=recent_activity_links)


@cq_blueprint.route('/sd/<string:link_id>', methods=['GET', 'POST'])
def start_draw(link_id):
    # 使用 link_id 的前 3 位作为键从 activity_data 中获取 n
    activity_id = link_id[:3]
    activity_info = activity_data.get(activity_id)
    n = activity_info['num_of_people'] if activity_info else None
    user_number = user_numbers.get(link_id)  # 获取用户编号

    draw_status_id = f"{activity_id}_{user_number}"
    local_draw_status = draw_status.get(draw_status_id, False)

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
            return redirect(url_for('cq.start_draw', link_id=link_id))

        else:
            return render_template('draw.html', link_id=link_id, local_draw_status=local_draw_status, user_number=user_number, activity_id=activity_id)

    if local_draw_status or request.method == 'POST':
        # 如果已经进行了队伍分配，直接显示抽签结果
        group_assignment = activity_group_assignments.get(activity_id)
        group_number = group_assignment.get(user_number)
        draw_status[draw_status_id] = True
        return render_template('draw_result.html', link_id=link_id, group_number=group_number, user_number=user_number, activity_id=activity_id)
    else:
        return render_template('draw.html', link_id=link_id, local_draw_status=local_draw_status, user_number=user_number, activity_id=activity_id)



@cq_blueprint.route('/ar/<string:activity_id>', methods=['GET'])
def activity_results(activity_id):
    # 获取指定活动的队伍分配数据
    group_assignment = activity_group_assignments.get(activity_id)
    n = activity_data.get(activity_id)

    if group_assignment is not None:
        # 创建一个空列表来保存每个用户的抽签结果
        user_draw_results = []

        # 遍历队伍分配数据，查找每个用户的抽签结果
        for user_number, group_number in group_assignment.items():
            draw_status_id= f"{activity_id}_{user_number}"

            local_draw_status = draw_status.get(draw_status_id, False)

            if local_draw_status:
                user_draw_result = f"用户 {user_number}: 组 {group_number}"
            else:
                user_draw_result = f"用户 {user_number}: 未抽签"
            user_draw_results.append(user_draw_result)

        # 按用户编号从小到大排序
        user_draw_results.sort(key=lambda x: int(re.search(r'\d+', x).group()))
        return render_template('activity_results.html', user_draw_results=user_draw_results)
    elif n is not None:
        # 创建一个空列表来保存每个用户的抽签结果
        user_draw_results = []

        # 生成 n 个 "用户 X: 未抽签" 的字符串
        for user_number in range(1, n + 1):
            user_draw_result = f"用户 {user_number}: 未抽签"
            user_draw_results.append(user_draw_result)
        return render_template('activity_results.html', user_draw_results=user_draw_results)
    else:
        return "无效的活动 ID"        


if __name__ == '__main__':
    app.register_blueprint(cq_blueprint)
    app.run(port=5001)

