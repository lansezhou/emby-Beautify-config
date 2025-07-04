from flask import Flask, request, abort, g
import requests
from os import environ
import yaml
from datetime import datetime, timezone, timedelta
import ssl
import logging
from functools import wraps
import json
import time
import re
from urllib.parse import quote
from jinja2 import Template

# 初始化Flask应用
app = Flask(__name__)

# ============= 日志配置 =============
if not app.debug:
    # 生产环境日志配置
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        filename='/var/log/emby_webhook.log',
        filemode='a'
    )
else:
    # 开发环境更详细的日志
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s - [%(filename)s:%(lineno)d]'
    )

logger = logging.getLogger(__name__)

# 修复SSL验证问题
ssl._create_default_https_context = ssl._create_unverified_context

# ============= 配置函数 =============
def get_config():
    """统一配置加载函数，减少重复代码"""
    try:
        with open("/config/config.yaml") as data:
            info_data = yaml.safe_load(data)
            
        # Telegram配置
        tg_config = {
            "token": info_data["token"][0],
            "admins": info_data.get("admins", []),
            "users": info_data.get("users", [])
        }
        
        # Emby配置
        emby_url = info_data["emby-server"][0]
        
        # 企业微信配置
        wecom_config = {
            "corp_id": info_data.get("wecom_corp_id", [""])[0],
            "secret": info_data.get("wecom_secret", [""])[0],
            "agent_id": info_data.get("wecom_agent_id", [""])[0],
            "proxy_url": info_data.get("wecom_proxy_url", [""])[0],
            "to_user": info_data.get("wecom_to_user", ["@all"])[0]
        }
        
        # Discord配置
        discord_config = {
            "webhook_url": info_data.get("discord_webhook_url", [""])[0],
            "username": info_data.get("discord_username", ["Emby通知"])[0],
            "avatar_url": info_data.get("discord_avatar_url", [""])[0]
        }
        
        # TMDB配置
        tmdb_config = {
            "api_key": info_data.get("tmdb_api_key", [""])[0],
            "image_base_url": info_data.get("tmdb_image_base_url", ["https://image.tmdb.org/t/p/original"])[0]
        }
        
        # 通知模板
        notification_templates = info_data.get("notification_templates", {
            "default": {
                "title": "{% if action == '新入库' and media_type == '电影' %}🎬 {% elif action == '新入库' and media_type == '剧集' %}📺 {% elif action == '新入库' and media_type == '有声书' %}📚 {% elif action == '新入库' %}🆕 {% elif action == '测试' %}🧪 {% elif action == '开始播放' %}▶️ {% elif action == '停止播放' %}⏹️ {% elif action == '登录成功' %}✅ {% elif action == '登录失败' %}❌ {% elif action == '标记了' %}🏷️ {% endif %}{% if user_name %}【{{ user_name }}】{% endif %}{{ action }}{% if media_type %} {{media_type}} {% endif %}{{ item_name }}",
                "text": (
                    "{% if vote_average %}⭐ 评分：{{ vote_average }}/10\n{% endif %}"
                    "📚 类型：{{ media_type }}\n"
                    "{% if percentage %}🔄 进度：{{ percentage }}%\n{% endif %}"
                    "{% if ip_address %}🌐 IP地址：{{ ip_address }}\n{% endif %}"
                    "{% if device_name %}📱 设备：{{ client }} {{ device_name }}\n{% endif %}"
                    "{% if total_size %}💾 大小：{{ total_size }}\n{% endif %}"
                    "{% if tmdbid %}🎬 TMDB ID：{{ tmdbid }}\n{% endif %}"
                    "{% if imdbid %}🎞️  IMDB ID：{{ imdbid }}\n{% endif %}"
                    "🕒 时间：{{ now_time }}\n"
                    "{% if overview %}\n📝 剧情：{{ overview }}{% endif %}"
                )
            }
        })
        
        return {
            "tg_config": tg_config,
            "emby_url": emby_url,
            "wecom_config": wecom_config,
            "discord_config": discord_config,
            "tmdb_config": tmdb_config,
            "notification_templates": notification_templates
        }
        
    except Exception as e:
        logger.error(f"加载配置失败: {str(e)}")
        raise

# 初始化全局配置
try:
    config = get_config()
    tg_config = config["tg_config"]
    t_token = tg_config["token"]
    tg_admins = tg_config["admins"]
    tg_users = tg_config["users"]
    
    url_send_photo = f"https://api.telegram.org/bot{t_token}/sendPhoto"
    url_send_message = f"https://api.telegram.org/bot{t_token}/sendMessage"
    e_server = config["emby_url"]
    wecom_config = config["wecom_config"]
    discord_config = config["discord_config"]
    tmdb_config = config["tmdb_config"]
    TMDB_API_KEY = tmdb_config["api_key"]
    TMDB_IMAGE_BASE_URL = tmdb_config["image_base_url"]
    notification_templates = config["notification_templates"]
except Exception as e:
    logger.error(f"初始化配置失败: {str(e)}")
    raise

# ============= 日志装饰器 =============
def log_webhook(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        logger.info(f"Incoming request headers: {dict(request.headers)}")
        logger.info(f"Request parameters: {request.args}")
        try:
            if request.data:
                logger.debug(f"Raw request data: {request.data.decode('utf-8')}")
        except Exception as e:
            logger.warning(f"Failed to log request data: {str(e)}")
        
        response = f(*args, **kwargs)
        logger.info(f"Response: {response}")
        return response
    return decorated_function

# ============= 设备识别 =============
def get_device_info(response):
    """优化版设备信息识别，增强IP和原始数据显示"""
    logger.debug("完整响应数据结构:\n%s", json.dumps(response, indent=2, ensure_ascii=False))
    
    # 1. 设备信息识别
    device_name = (
        response.get("DeviceName") 
        or response.get("Client")  # 新增Client字段检查
        or response.get("Session", {}).get("DeviceName")
        or response.get("Device", {}).get("DeviceName")
        or "未知设备"
    )
    device_info = "未知设备"
    for field in ["Client", "DeviceName", "Session.DeviceName", "Device.DeviceName"]:
        if field in response:
            device_info = response[field]
            break
        elif "Device" in response and field.split(".")[-1] in response["Device"]:
            device_info = response["Device"][field.split(".")[-1]]
            break
        elif "Session" in response and field.split(".")[-1] in response["Session"]:
            device_info = response["Session"][field.split(".")[-1]]
            break

    # 2. IP地址提取（增强版）
    ip_address = ""
    ip_sources = [
        "RemoteEndPoint",
        "Session.RemoteEndPoint",
        "PlaybackInfo.RemoteEndPoint",
        "Device.RemoteEndPoint"
        "Server.RemoteAddress",
        "Request.RemoteAddress"
    ]
    
    for source in ip_sources:
        try:
            keys = source.split(".")
            value = response
            for key in keys:
                value = value.get(key, {})
            
            if value and isinstance(value, str):
                ip = value.split(":")[0] if ":" in value else value
                if ip and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                    ip_address = ip
                    break
        except Exception:
            continue

    # 3. 原始数据收集（过滤空值）
    relevant_data = {
        k: v for k, v in response.items()
        if any(keyword in k.lower() for keyword in ["device", "client", "ip", "remote", "session"])
        and v
    }
    
    for section in ["Device", "Session", "PlaybackInfo"]:
        if section in response:
            relevant_data.update({
                f"{section}.{k}": v 
                for k, v in response[section].items()
                if any(keyword in k.lower() for keyword in ["device", "client", "ip", "remote"])
                and v
            })

    result = {
        "device_name": device_name,
        "ip_address": ip_address,
        "raw_data": response if app.debug else None
    }
    
    logger.info("设备识别结果: %s", result)
    return result

def get_ip_location(ip):
    """使用 ip-api.com 获取地理位置"""
    if not ip:
        return None
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}?lang=zh-CN", timeout=3)
        data = res.json()
        if data['status'] == 'success':
            return f"{data.get('regionName', '')} {data.get('city', '')}".strip()
    except Exception as e:
        logger.warning(f"获取IP位置失败: {str(e)}")
    return None

# ============= TMDB图片获取 =============
def get_tmdb_image_url(item):
    """增强版的TMDB图片获取函数，支持多级回退策略"""
    if not TMDB_API_KEY:
        logger.warning("TMDB API密钥未配置，跳过TMDB图片查询")
        return get_emby_local_image(item)
    
    try:
        # 电视剧处理逻辑
        if item["Type"] == "Episode":
            return get_episode_image(item)
        
        # 电影处理逻辑
        elif item["Type"] == "Movie":
            return get_movie_image(item)
            
    except Exception as e:
        logger.error(f"获取TMDB图片时发生错误: {str(e)}")
        return get_emby_local_image(item)

def get_episode_image(item):
    """通过剧集名称和年份从TMDB搜索匹配海报（增强版）"""
    series_name = item.get("SeriesName", "").strip()
    if not series_name:
        logger.debug(f"缺少剧集名称，完整数据:\n{json.dumps(item, indent=2)}")
        return get_emby_local_image(item)

    year = item.get("ProductionYear")
    logger.info(f"正在搜索TMDB: {series_name} ({year if year else '无年份'})")

    try:
        # 1. 构建搜索参数
        params = {
            "api_key": TMDB_API_KEY,
            "query": quote(series_name),
            "language": "zh-CN",
            "include_adult": "false"
        }
        if year:
            params["first_air_date_year"] = year  # 更精确的年份参数

        # 2. 执行搜索
        search_url = "https://api.themoviedb.org/3/search/tv"
        resp = requests.get(search_url, params=params, timeout=10)
        
        if resp.status_code != 200:
            logger.error(f"TMDB搜索失败 HTTP {resp.status_code}")
            return get_emby_local_image(item)

        results = resp.json().get("results", [])
        if not results:
            logger.debug("TMDB无匹配结果，尝试不带年份搜索" if year else "TMDB无匹配结果")
            return get_emby_local_image(item)

        # 3. 选择最佳匹配（按流行度排序）
        best_match = max(results, key=lambda x: x.get("popularity", 0))
        series_id = best_match.get("id")
        
        # 4. 获取海报
        detail_url = f"https://api.themoviedb.org/3/tv/{series_id}"
        detail_resp = requests.get(
            detail_url,
            params={"api_key": TMDB_API_KEY},
            timeout=10
        )
        
        if detail_resp.status_code == 200:
            poster_path = detail_resp.json().get("poster_path")
            if poster_path:
                image_url = f"{TMDB_IMAGE_BASE_URL}{poster_path}"
                logger.info(f"TMDB匹配成功: {image_url}")
                return image_url

    except Exception as e:
        logger.error(f"TMDB处理异常: {str(e)}", exc_info=True)
    
    return get_emby_local_image(item)

def get_movie_image(item):
    """获取电影图片"""
    tmdb_id = item.get("ProviderIds", {}).get("Tmdb")
    if not tmdb_id:
        logger.debug("电影缺少TMDB ID，无法从TMDB获取图片")
        return get_emby_local_image(item)
    
    url = f"https://api.themoviedb.org/3/movie/{tmdb_id}?api_key={TMDB_API_KEY}"
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            poster_path = response.json().get("poster_path")
            if poster_path:
                return f"{TMDB_IMAGE_BASE_URL}{poster_path}"
    except Exception as e:
        logger.warning(f"获取电影海报失败: {str(e)}")
    
    return get_emby_local_image(item)

def get_emby_local_image(item):
    """获取Emby本地图片"""
    try:
        item_id = item["Id"]
        # 尝试主图
        image_url = f"{e_server}/Items/{item_id}/Images/Primary?fillHeight=600&fillWidth=400&quality=90"
        if verify_image_url(image_url):
            return image_url
        
        # 尝试背景图
        image_url = f"{e_server}/Items/{item_id}/Images/Backdrop?fillHeight=600&fillWidth=400&quality=90"
        if verify_image_url(image_url):
            return image_url
            
    except Exception as e:
        logger.error(f"获取Emby本地图片失败: {str(e)}")
    
    return None

def verify_image_url(url):
    """验证图片URL是否有效"""
    try:
        response = requests.head(url, timeout=5)
        return response.status_code == 200
    except Exception:
        return False

# ============= 企业微信通知 =============
def send_wecom_message(content, image_url=None, title="Emby播放通知"):
    if not all(wecom_config.values()):
        logger.warning(f"企业微信配置不完整: {wecom_config}")
        return

    max_retries = 2
    for attempt in range(max_retries):
        try:
            token_url = f"{wecom_config['proxy_url'] or 'https://qyapi.weixin.qq.com'}/cgi-bin/gettoken"
            params = {
                "corpid": wecom_config["corp_id"],
                "corpsecret": wecom_config["secret"]
            }
            token_res = requests.get(token_url, params=params, timeout=10)
            token_data = token_res.json()
            if token_data.get("errcode") != 0:
                continue

            send_url = f"{wecom_config['proxy_url'] or 'https://qyapi.weixin.qq.com'}/cgi-bin/message/send"

            if image_url:
                # 图文消息 news 类型（带图片）
                data = {
                    "touser": wecom_config["to_user"],
                    "msgtype": "news",
                    "agentid": wecom_config["agent_id"],
                    "news": {
                        "articles": [{
                            "title": title,
                            "description": content,
                            "url": image_url,
                            "picurl": image_url
                        }]
                    }
                }
            else:
                # 使用 textcard 类型（支持标题，无图也美观）
                data = {
                    "touser": wecom_config["to_user"],
                    "msgtype": "textcard",
                    "agentid": wecom_config["agent_id"],
                    "textcard": {
                        "title": title,
                        "description": content.replace('\n', '<br>'),
                        "url": "https://example.com",  # 你可以替换成 Emby 或其他页面
                        "btntxt": "详情"
                    }
                }

            send_res = requests.post(send_url, params={"access_token": token_data["access_token"]}, json=data, timeout=10)
            if send_res.json().get("errcode") == 0:
                logger.info("企业微信通知发送成功")
                break
            else:
                logger.error(f"企业微信消息发送失败: {send_res.json()}")
        except Exception as e:
            logger.error(f"企业微信通知异常(尝试 {attempt + 1}/{max_retries}): {str(e)}")


# ============= Discord通知 =============
def send_discord_message(content, image_url=None, title="Emby播放通知"):
    if not discord_config.get("webhook_url"):
        logger.warning("Discord webhook未配置")
        return

    try:
        payload = {
            "username": discord_config["username"],
            "content": f"**{title}**\n{content}",
            "avatar_url": discord_config.get("avatar_url")
        }

        if image_url:
            payload["embeds"] = [{
                "image": {"url": image_url},
                "color": 0x00ff00
            }]

        response = requests.post(
            discord_config["webhook_url"],
            json=payload,
            timeout=10
        )

        if response.status_code == 204:
            logger.info("Discord通知发送成功")
        else:
            logger.error(f"Discord通知发送失败: {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"发送Discord通知时出错: {str(e)}")

# ============= 播放状态处理 =============
def get_event_action(event_type):
    actions = {
        "playback.start": "开始播放",
        "playback.stop": "停止播放",
        "playback.pause": "暂停播放",
        "playback.unpause": "继续播放",
        "library.deleted": "删除内容",
        "item.markunplayed": "标记未播放",
        "item.markplayed": "标记已播放",
        "system.updateavailable": "系统更新可用",
        "system.serverrestartrequired": "需要重启服务器",
        "user.authenticationfailed": "登录失败",
        "user.authenticated": "登录成功",
        "plugins.pluginuninstalled": "插件卸载",
        "plugins.plugininstalled": "插件安装",
        "device.online": "设备上线",
        "device.offline": "设备离线",
    }
    return actions.get(event_type, event_type)

def format_progress(position_ticks, runtime_ticks):
    if runtime_ticks > 0:
        progress_sec = position_ticks / 10_000_000
        runtime_sec = runtime_ticks / 10_000_000
        progress_percent = int((position_ticks / runtime_ticks) * 100)
        return progress_percent
    return 0

def build_message(response):
    event = response.get("Event")
    if not event:
        logger.error("缺少事件类型")
        return None, None, None

    user = response.get("User", {})
    user_name = user.get("Name", "未知用户")
    device_info = get_device_info(response)

    beijing_time = datetime.now(timezone.utc) + timedelta(hours=8)
    time_str = beijing_time.strftime('%Y-%m-%d %H:%M:%S')

    # 基础模板上下文
    context = {
        "action": get_event_action(event),
        "user_name": user_name,
        "now_time": time_str,
        "ip_address": device_info.get("ip_address", ""),
        "ip_location": get_ip_location(device_info.get("ip_address", "")),
        "client": device_info.get("device_name", "未知设备"),
        "device_name": device_info.get("device_name", "未知设备"),
    }

    # 播放事件
    playback_events = ("playback.start", "playback.stop", "playback.pause", "playback.unpause")
    if event in playback_events:
        item = response.get("Item", {})
        if not item:
            logger.warning("缺少媒体项信息")
            return None, None, None

        media_type = "电影" if item.get("Type") == "Movie" else "剧集"
        media_name = item.get("Name", "未知媒体")
        media_year = item.get("ProductionYear", "未知年份")

        if item.get("Type") == "Episode":
            series_name = item.get("SeriesName", "未知剧集")
            season_num = item.get("ParentIndexNumber", 0)
            episode_num = item.get("IndexNumber", 0)
            media_name = f"{series_name} S{season_num}E{episode_num} - {item.get('Name')}"

        playback_info = response.get("PlaybackInfo", {})
        position_ticks = playback_info.get("PositionTicks", 0)
        runtime_ticks = item.get("RunTimeTicks", 0)
        progress_percent = format_progress(position_ticks, runtime_ticks)
        if progress_percent > 0:
            context["percentage"] = progress_percent

        context.update({
            "media_type": media_type,
            "item_name": f"{media_name} ({media_year})",
            "overview": item.get("Overview", "暂无剧情简介"),
            "vote_average": item.get("CommunityRating"),
            "tmdbid": item.get("ProviderIds", {}).get("Tmdb"),
            "imdbid": item.get("ProviderIds", {}).get("Imdb"),
            "resource_quality": playback_info.get("VideoQuality"),
            "season_episode": f"S{season_num}E{episode_num}" if item.get("Type") == "Episode" else ""
        })

        template = notification_templates.get("playback", notification_templates["default"])
        title_template = Template(template["title"])
        text_template = Template(template["text"])

        message = text_template.render(context)
        title = title_template.render(context)
        image_url = get_tmdb_image_url(item)
        return message, image_url, title

    # 入库事件
    elif event == "library.new":
        item = response.get("Item", {})
        if not item:
            logger.warning("缺少媒体项信息")
            return None, None, None

        # 确定媒体类型
        if item.get("Type") == "Movie":
            media_type = "电影"
        elif item.get("Type") == "Episode":
            media_type = "剧集"
        elif item.get("Type") == "Audio":
            media_type = "有声书"
        else:
            media_type = item.get("Type", "媒体")

        # 构建媒体名称
        media_name = item.get("Name", "未知媒体")
        if media_type == "剧集":
            series_name = item.get("SeriesName", "未知剧集")
            season_num = item.get("ParentIndexNumber", 0)
            episode_num = item.get("IndexNumber", 0)
            media_name = f"{series_name} S{season_num}E{episode_num} - {media_name}"

        context.update({
            "media_type": media_type,
            "item_name": f"{media_name} ({item.get('ProductionYear', '未知年份')})",
            "overview": item.get("Overview", "暂无剧情简介"),
            "vote_average": item.get("CommunityRating"),
            "tmdbid": item.get("ProviderIds", {}).get("Tmdb"),
            "imdbid": item.get("ProviderIds", {}).get("Imdb"),
            "total_size": format_size(item.get("Size", 0)) if item.get("Size") else None
        })

        template = notification_templates.get("library", notification_templates["default"])
        title_template = Template(template["title"])
        text_template = Template(template["text"])

        message = text_template.render(context)
        title = title_template.render(context)
        image_url = get_tmdb_image_url(item)
        wechat_message = text_template.render(context)
        return wechat_message, image_url, title  # 微信用message，其他用title

    # 登录事件
    elif event in ("user.authenticated", "user.authenticationfailed"):
        template_name = "login"
        context.update({
            "client": device_info.get("device_name", "未知设备").split()[0],
            "device_name": device_info.get("device_name", "未知设备"),
            "ip_location": get_ip_location(device_info.get("ip_address", "")),
        })
        template = notification_templates.get(template_name, notification_templates["default"])
        title_template = Template(template["title"])
        text_template = Template(template["text"])

        message = text_template.render(context)
        title = title_template.render(context)
        return message, None, title

    # 标记事件
    elif event.startswith("item.mark") or event.startswith("user.rating") or event == "item.rate":
        logger.debug(f"【标记事件原始数据】\n{json.dumps(response, indent=2)}")

        item = response.get("Item", {})
        if not item:
            logger.error("标记事件缺少Item字段！")
            return None, None, None

        if item.get("Type") == "Movie":
            media_type = "电影"
        elif item.get("Type") == "Episode":
            media_type = "剧集"
        else:
            media_type = item.get("Type", "媒体")

        media_name = item.get("Name", "未知媒体")
        if item.get("Type") == "Episode":
            series_name = item.get("SeriesName", "未知剧集")
            season_num = item.get("ParentIndexNumber", 0)
            episode_num = item.get("IndexNumber", 0)
            media_name = f"{series_name} S{season_num}E{episode_num} - {media_name}"

        rating = item.get("CommunityRating") or response.get("Rating")
        if rating:
            try:
                rating = float(rating)
                rating_str = f"{rating:.1f}/10"
            except (ValueError, TypeError):
                rating_str = str(rating)
        else:
            rating_str = None

        if event == "item.markplayed":
            action = "标记为已播放"
            emoji = "✅"
        elif event == "item.markunplayed":
            action = "标记为未播放"
            emoji = "🔄"
        elif event.startswith("user.rating") or event == "item.rate":
            action = f"评分 {rating_str}" if rating_str else "评分"
            emoji = "⭐"
        else:
            action = "标记了"
            emoji = "🏷️"

        context = {
            "action": action,
            "mark_emoji": emoji,
            "mark_type": action,
            "media_type": media_type,
            "item_name": f"{media_name} ({item.get('ProductionYear', '未知年份')})",
            "user_name": response.get("User", {}).get("Name", "未知用户"),
            "device_name": device_info.get("device_name", "未知设备"),
            "ip_address": device_info.get("ip_address", ""),
            "now_time": time_str,
            "overview": item.get("Overview", "暂无剧情简介"),
            "vote_average": rating_str,
            "tmdbid": item.get("ProviderIds", {}).get("Tmdb"),
            "imdbid": item.get("ProviderIds", {}).get("Imdb")
        }

        template = notification_templates.get("mark", notification_templates["default"])
        title_template = Template(template["title"])
        text_template = Template(template["text"])

        message = text_template.render(context)
        title = title_template.render(context)
        image_url = get_tmdb_image_url(item)

        return message, image_url, title

    # 默认事件
    else:
        template = notification_templates.get("default")
        title_template = Template(template["title"])
        text_template = Template(template["text"])

        message = text_template.render(context)
        title = title_template.render(context)
        return message, None, title


def send_message():
    try:
        # 获取并验证响应数据
        response = g.get('response_data')
        if not response:
            logger.error("No response data available")
            return
            
        event = response.get("Event", "")
        message, image_url, title = build_message(response)
        if not message:
            return

        # ==================== Telegram通知 ====================
        full_message = f"<b>{title}</b>\n{message}"
        all_recipients = tg_admins + tg_users
        
        for chat_id in all_recipients:
            for attempt in range(3):  # 最大重试3次
                try:
                    if image_url:
                        resp = requests.post(
                            url_send_photo,
                            data={
                                "chat_id": chat_id,
                                "photo": image_url,
                                "caption": full_message,
                                "parse_mode": "HTML",
                                "disable_notification": False
                            },
                            timeout=15
                        )
                    else:
                        resp = requests.post(
                            url_send_message,
                            data={
                                "chat_id": chat_id,
                                "text": full_message,
                                "parse_mode": "HTML",
                                "disable_web_page_preview": True
                            },
                            timeout=15
                        )
                    
                    if resp.status_code == 200 and resp.json().get("ok"):
                        logger.info(f"Telegram通知发送成功至 {chat_id}")
                        break
                        
                    logger.error(f"Telegram响应异常: {resp.text}")
                    if image_url and attempt == 1:  # 最后一次尝试用纯文本
                        requests.post(
                            url_send_message,
                            data={"chat_id": chat_id, "text": full_message, "parse_mode": "HTML"},
                            timeout=10
                        )
                        break
                        
                except Exception as e:
                    if attempt == 2:
                        logger.error(f"发送至 {chat_id} 失败(最终尝试): {str(e)}")
                    else:
                        time.sleep((attempt + 1) * 2)
                        logger.warning(f"发送至 {chat_id} 失败(尝试 {attempt+1}): {str(e)}")

        # ==================== 企业微信通知 ====================
        required_wecom_fields = ["corp_id", "secret", "agent_id", "to_user"]
        if all(wecom_config.get(field) for field in required_wecom_fields):
            try:
                logger.debug(f"准备企业微信通知 - 事件: {event}")
                
                # 处理特殊事件标题
                final_title = title
                if event == "user.authenticated":
                    if not any(marker in title for marker in ["🔑", "✅"]):
                        final_title = f"🔑+🔓 {title}"
                elif event == "user.authenticationfailed":
                    if not any(marker in title for marker in ["🔑", "❌"]):
                        final_title = f"🔑🔒 {title}"
                
                # 发送通知
                send_wecom_message(
                    content=message,  # 使用原始消息内容
                    image_url=image_url,
                    title=final_title
                )
                
                logger.info(f"企业微信通知发送成功 - 标题: {final_title}")
                
            except Exception as e:
                logger.error(f"企业微信通知失败: {str(e)}", exc_info=True)
        else:
            missing = [f for f in required_wecom_fields if not wecom_config.get(f)]
            logger.warning(f"企业微信配置缺失: {missing}")

        # ==================== Discord通知 ====================
        if discord_config.get("webhook_url"):
            try:
                send_discord_message(message, image_url, title)
                logger.info("Discord通知发送成功")
            except Exception as e:
                logger.error(f"Discord通知失败: {str(e)}")

    except Exception as e:
        logger.exception("通知发送全局错误: {str(e)}")

# ============= Webhook 路由 =============
@app.route('/webhook', methods=['POST'])
@log_webhook
def handle_webhook():
    try:
        logger.debug("完整请求体: %s", request.get_data(as_text=True))
        
        if not request.is_json:
            abort(400, "Request must be JSON")
        
        data = request.get_json()
        if not data:
            abort(400, "Invalid JSON data")
        
        logger.info("事件类型: %s", data.get("Event", "未提供"))
        
        g.response_data = data
        send_message()
        
        return {"status": "success", "message": "Webhook processed"}, 200
    
    except Exception as e:
        logger.exception("处理webhook时发生严重错误")
        abort(500, f"Internal Server Error: {str(e)}")

# ============= 配置热重载路由 =============
@app.route('/reload_config')
def reload_config():
    """重新加载配置文件"""
    global config, tg_config, t_token, tg_admins, tg_users, url_send_photo, url_send_message, e_server, wecom_config, discord_config, tmdb_config, TMDB_API_KEY, TMDB_IMAGE_BASE_URL, notification_templates
    
    logger.info("收到配置重载请求")
    try:
        config = get_config()
        # 重新初始化所有全局变量
        tg_config = config["tg_config"]
        t_token = tg_config["token"]
        tg_admins = tg_config["admins"]
        tg_users = tg_config["users"]
        
        url_send_photo = f"https://api.telegram.org/bot{t_token}/sendPhoto"
        url_send_message = f"https://api.telegram.org/bot{t_token}/sendMessage"
        e_server = config["emby_url"]
        wecom_config = config["wecom_config"]
        discord_config = config["discord_config"]
        tmdb_config = config["tmdb_config"]
        TMDB_API_KEY = tmdb_config["api_key"]
        TMDB_IMAGE_BASE_URL = tmdb_config["image_base_url"]
        notification_templates = config["notification_templates"]
        
        logger.info("配置重载成功")
        return {"status": "success", "message": "Configuration reloaded successfully"}, 200
    except Exception as e:
        logger.error(f"配置重载失败: {str(e)}")
        return {"status": "error", "message": str(e)}, 500
        
# ============= 健康检查路由 =============
@app.route('/healthcheck')
def healthcheck():
    return {
        "status": "healthy",
        "time_utc": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        "time_beijing": (datetime.now(timezone.utc) + timedelta(hours=8)).strftime('%Y-%m-%d %H:%M:%S')
    }, 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)