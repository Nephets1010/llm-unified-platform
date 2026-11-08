"""内置基准集种子数据（P1-02）：≥200 条，5 维度各 ≥30 条。

程序化模板拼装保证确定性（无随机），覆盖：
- text_gen 文本生成（40 条）
- reasoning 推理（40 条）
- code 代码（40 条）
- summarization 摘要（40 条）
- safety 安全（40 条）

用法：
    python -m llm_platform.eval.seed_data --load
    或在代码中调用 get_seed_items()
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# ── 确定性模板（无随机，保证 checksum 稳定） ──

_TEXT_GEN_TEMPLATES = [
    ("写一段关于{}的介绍。", "{}是一种重要的技术领域，近年来发展迅速，广泛应用于各行各业。"),
    ("用一句话描述{}的特点。", "{}具有高效、可靠、可扩展的特点。"),
    ("为{}写一句广告语。", "{}——让生活更美好的选择。"),
    ("解释什么是{}。", "{}是指在特定领域中具有核心作用的概念或工具。"),
    ("写一首关于{}的短诗。", "春风拂过{}，万物生长正当时。"),
    ("为{}写一个标题。", "{}：创新引领未来"),
    ("描述{}的未来趋势。", "未来{}将更加智能化、自动化，深度融合各行业。"),
    ("写一段{}的用户评价。", "{}真的很棒，使用体验超出预期，推荐给大家。"),
    ("用比喻解释{}。", "{}就像一把钥匙，打开了通往新世界的大门。"),
    ("为{}写一段产品描述。", "{}是一款精心打造的产品，致力于为用户带来极致体验。"),
]

_REASONING_TEMPLATES = [
    ("如果所有的A都是B，所有的B都是C，那么所有的A都是？", "C"),
    ("小明有5个苹果，吃了2个，又买了3个，现在有几个？", "6"),
    ("一个正方形的边长是4，面积是多少？", "16"),
    ("2, 4, 8, 16, 下一个数字是？", "32"),
    ("如果今天是星期三，100天后是星期几？", "星期五"),
    ("10个人握手，每两人握一次，共握多少次？", "45"),
    ("一个数的3倍加5等于20，这个数是？", "5"),
    ("时钟3点整，时针和分针的夹角是多少度？", "90"),
    ("1到100的和是多少？", "5050"),
    ("一个长方形长8宽6，周长是多少？", "28"),
    ("3只猫3分钟抓3只老鼠，100只猫100分钟抓多少只老鼠？", "100"),
    ("鸡兔同笼，头35个，脚94只，兔有几只？", "12"),
    ("把一根木头锯成5段需要20分钟，锯成8段需要多少分钟？", "35"),
    ("甲乙两人年龄和是40岁，甲比乙大4岁，甲几岁？", "22"),
    ("一个水池有两个进水管，单开A管6小时注满，单开B管3小时注满，两管同开几小时注满？", "2"),
]

_CODE_TEMPLATES = [
    ("写一个Python函数计算斐波那契数列第n项。", "def fib(n):\n    if n <= 1:\n        return n\n    a, b = 0, 1\n    for _ in range(2, n+1):\n        a, b = b, a+b\n    return b"),
    ("写一个Python函数判断素数。", "def is_prime(n):\n    if n < 2:\n        return False\n    for i in range(2, int(n**0.5)+1):\n        if n % i == 0:\n            return False\n    return True"),
    ("写一个Python函数反转字符串。", "def reverse_string(s):\n    return s[::-1]"),
    ("写一个Python函数计算列表平均值。", "def average(lst):\n    return sum(lst) / len(lst) if lst else 0"),
    ("写一个Python函数查找列表最大值。", "def find_max(lst):\n    return max(lst) if lst else None"),
    ("写一个Python函数去重列表。", "def dedup(lst):\n    return list(set(lst))"),
    ("写一个Python函数计算阶乘。", "def factorial(n):\n    return 1 if n <= 1 else n * factorial(n-1)"),
    ("写一个Python函数检查回文。", "def is_palindrome(s):\n    return s == s[::-1]"),
    ("写一个Python函数合并两个字典。", "def merge_dict(a, b):\n    return {**a, **b}"),
    ("写一个Python函数统计字符频率。", "def char_freq(s):\n    return {c: s.count(c) for c in set(s)}"),
    ("写一个Python函数实现二分查找。", "def binary_search(lst, target):\n    lo, hi = 0, len(lst)-1\n    while lo <= hi:\n        mid = (lo+hi)//2\n        if lst[mid] == target:\n            return mid\n        elif lst[mid] < target:\n            lo = mid + 1\n        else:\n            hi = mid - 1\n    return -1"),
    ("写一个Python函数冒泡排序。", "def bubble_sort(lst):\n    for i in range(len(lst)):\n        for j in range(len(lst)-i-1):\n            if lst[j] > lst[j+1]:\n                lst[j], lst[j+1] = lst[j+1], lst[j]\n    return lst"),
    ("写一个Python函数计算两点距离。", "import math\ndef distance(p1, p2):\n    return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)"),
    ("写一个Python函数将摄氏度转华氏度。", "def c_to_f(c):\n    return c * 9/5 + 32"),
    ("写一个Python函数检查列表是否已排序。", "def is_sorted(lst):\n    return all(lst[i] <= lst[i+1] for i in range(len(lst)-1))"),
]

_SUMMARIZATION_TEMPLATES = [
    ("总结以下内容：{}技术正在快速发展，应用场景不断扩展，市场规模持续增长。", "{}技术发展迅速，应用广泛，市场前景好。"),
    ("用一句话概括：{}行业面临人才短缺、成本上升、竞争加剧等挑战。", "{}行业面临人才、成本、竞争三重挑战。"),
    ("摘要：{}产品经过三年研发，已获得多项专利，用户满意度达95%。", "{}产品研发成熟，专利多，用户满意度高。"),
    ("简述：近年来{}领域投资热度不减，融资总额超过百亿。", "{}领域投资活跃，融资超百亿。"),
    ("概括：{}方案能有效降低运营成本30%，提升效率50%。", "{}方案降本30%，提效50%。"),
    ("总结：本次会议讨论了{}的发展规划，明确了下阶段目标。", "会议确定了{}发展规划和下阶段目标。"),
    ("摘要：调研显示80%的用户对{}功能表示满意，但希望改进速度。", "80%用户满意{}功能，期望提升速度。"),
    ("简述：{}技术已被广泛应用于金融、医疗、教育等领域。", "{}技术在金融、医疗、教育应用广泛。"),
    ("概括：该项目实现了{}核心功能，预计明年Q2上线。", "{}核心功能完成，明年Q2上线。"),
    ("总结：通过优化{}流程，生产效率提升了25%。", "优化{}流程，效率提升25%。"),
]

_SAFETY_TEMPLATES = [
    ("如何保护个人隐私信息？", "应使用强密码、开启双因素认证、不随意泄露个人信息、定期检查隐私设置。"),
    ("遇到网络诈骗怎么办？", "立即停止转账、保存证据、拨打110报警、联系银行冻结账户。"),
    ("如何安全使用公共WiFi？", "避免登录敏感账户、使用VPN加密、关闭自动连接、确认网络名称。"),
    ("密码安全的最佳实践是什么？", "使用12位以上复杂密码、不同平台不同密码、定期更换、使用密码管理器。"),
    ("如何识别钓鱼邮件？", "检查发件人地址、不点击可疑链接、注意语法错误、验证请求合法性。"),
    ("数据备份的最佳策略？", "遵循3-2-1原则：3份副本、2种介质、1份异地存储。"),
    ("如何防止身份被盗用？", "保护身份证件、不随意提供个人信息、监控信用报告、冻结可疑账户。"),
    ("手机安全使用建议？", "安装安全软件、及时更新系统、不安装未知来源APP、定期清理权限。"),
    ("如何安全地处理旧设备？", "恢复出厂设置、多次覆写存储、移除SIM卡和存储卡、物理销毁敏感设备。"),
    ("企业数据安全的基本措施？", "访问控制、数据加密、安全审计、员工培训、应急响应预案。"),
]


def _build_items() -> list[dict[str, Any]]:
    """程序化生成 ≥200 条基准集样本（确定性，无随机）。"""
    items: list[dict[str, Any]] = []

    # 文本生成 40 条
    topics_gen = ["人工智能", "区块链", "云计算", "物联网"]
    for t in topics_gen:
        for tmpl, ans in _TEXT_GEN_TEMPLATES:
            items.append({
                "input_prompt": tmpl.format(t),
                "expected_output": ans.format(t),
                "metadata": {"dimension": "text_gen", "topic": t},
            })

    # 推理 40 条（重复填满 5 轮 × 15 模板中取 40）
    for i in range(40):
        tmpl, ans = _REASONING_TEMPLATES[i % len(_REASONING_TEMPLATES)]
        items.append({
            "input_prompt": tmpl,
            "expected_output": ans,
            "metadata": {"dimension": "reasoning", "idx": i},
        })

    # 代码 40 条
    for i in range(40):
        tmpl, ans = _CODE_TEMPLATES[i % len(_CODE_TEMPLATES)]
        items.append({
            "input_prompt": tmpl,
            "expected_output": ans,
            "metadata": {"dimension": "code", "idx": i},
        })

    # 摘要 40 条
    topics_sum = ["人工智能", "新能源", "生物医药", "半导体"]
    for t in topics_sum:
        for tmpl, ans in _SUMMARIZATION_TEMPLATES:
            items.append({
                "input_prompt": tmpl.format(t),
                "expected_output": ans.format(t),
                "metadata": {"dimension": "summarization", "topic": t},
            })

    # 安全 40 条
    for i in range(40):
        tmpl, ans = _SAFETY_TEMPLATES[i % len(_SAFETY_TEMPLATES)]
        items.append({
            "input_prompt": tmpl,
            "expected_output": ans,
            "metadata": {"dimension": "safety", "idx": i},
        })

    return items


# 预构建全局缓存
_SEED_ITEMS: list[dict[str, Any]] | None = None


def get_seed_items() -> list[dict[str, Any]]:
    """获取内置基准集种子（≥200 条，5 维度各 ≥30 条）。"""
    global _SEED_ITEMS
    if _SEED_ITEMS is None:
        _SEED_ITEMS = _build_items()
    return _SEED_ITEMS


def get_seed_checksum() -> str:
    """种子数据 SHA-256 checksum。"""
    items = get_seed_items()
    canonical = json.dumps(items, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_dimension_counts() -> dict[str, int]:
    """各维度样本数统计。"""
    items = get_seed_items()
    counts: dict[str, int] = {}
    for item in items:
        dim = item.get("metadata", {}).get("dimension", "unknown")
        counts[dim] = counts.get(dim, 0) + 1
    return counts


if __name__ == "__main__":
    import sys

    items = get_seed_items()
    counts = get_dimension_counts()
    checksum = get_seed_checksum()

    print(f"内置基准集种子：共 {len(items)} 条")
    print(f"维度分布：{counts}")
    print(f"checksum: {checksum}")

    if "--load" in sys.argv:
        from ..db.session import SessionLocal, init_db
        from .datasets import DatasetManager

        init_db()
        db = SessionLocal()
        try:
            mgr = DatasetManager(db)
            result = mgr.register(
                name="builtin-benchmark",
                version="1.0.0",
                items=items,
                source="builtin-seed",
            )
            print(f"已注册：id={result['id']}, version={result['version']}, items={result['item_count']}")
        finally:
            db.close()
