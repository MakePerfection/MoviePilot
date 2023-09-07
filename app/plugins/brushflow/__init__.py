import re
import threading
import time
from datetime import datetime, timedelta
from threading import Event
from typing import Any, List, Dict, Tuple, Optional, Union

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

from app import schemas
from app.chain.search import SearchChain
from app.chain.torrents import TorrentsChain
from app.core.config import settings
from app.db.site_oper import SiteOper
from app.helper.sites import SitesHelper
from app.log import logger
from app.modules.qbittorrent import Qbittorrent
from app.modules.transmission import Transmission
from app.plugins import _PluginBase
from app.schemas import Notification, NotificationType, TorrentInfo
from app.utils.string import StringUtils

lock = threading.Lock()


class BrushFlow(_PluginBase):
    # 插件名称
    plugin_name = "站点刷流"
    # 插件描述
    plugin_desc = "自动托管刷流，将会默认提高对应站点的种子刷新频率。"
    # 插件图标
    plugin_icon = "fileupload.png"
    # 主题色
    plugin_color = "#EC5665"
    # 插件版本
    plugin_version = "1.0"
    # 插件作者
    plugin_author = "jxxghp"
    # 作者主页
    author_url = "https://github.com/jxxghp"
    # 插件配置项ID前缀
    plugin_config_prefix = "brushflow_"
    # 加载顺序
    plugin_order = 21
    # 可使用的用户级别
    auth_level = 3

    # 私有属性
    siteshelper = None
    siteoper = None
    torrents = None
    searchchain = None
    qb = None
    tr = None
    # 添加种子定时
    _cron = 10
    # 检查种子定时
    _check_interval = 5
    # 退出事件
    _event = Event()
    _scheduler = None
    _enabled = False
    _notify = True
    _onlyonce = False
    _brushsites = []
    _downloader = "qbittorrent"
    _disksize = 0
    _freeleech = "free"
    _maxupspeed = 0
    _maxdlspeed = 0
    _maxdlcount = 0
    _include = ""
    _exclude = ""
    _size = 0
    _seeder = 0
    _pubtime = 0
    _seed_time = 0
    _seed_ratio = 0
    _seed_size = 0
    _download_time = 0
    _seed_avgspeed = 0
    _seed_inactivetime = 0
    _up_speed = 0
    _dl_speed = 0
    _save_path = ""

    def init_plugin(self, config: dict = None):
        self.siteshelper = SitesHelper()
        self.siteoper = SiteOper()
        self.torrents = TorrentsChain()
        self.searchchain = SearchChain()
        if config:
            self._enabled = config.get("enabled")
            self._notify = config.get("notify")
            self._onlyonce = config.get("onlyonce")
            self._brushsites = config.get("brushsites")
            self._downloader = config.get("downloader")
            self._disksize = config.get("disksize")
            self._freeleech = config.get("freeleech")
            self._maxupspeed = config.get("maxupspeed")
            self._maxdlspeed = config.get("maxdlspeed")
            self._maxdlcount = config.get("maxdlcount")
            self._include = config.get("include")
            self._exclude = config.get("exclude")
            self._size = config.get("size")
            self._seeder = config.get("seeder")
            self._pubtime = config.get("pubtime")
            self._seed_time = config.get("seed_time")
            self._seed_ratio = config.get("seed_ratio")
            self._seed_size = config.get("seed_size")
            self._download_time = config.get("download_time")
            self._seed_avgspeed = config.get("seed_avgspeed")
            self._seed_inactivetime = config.get("seed_inactivetime")
            self._up_speed = config.get("up_speed")
            self._dl_speed = config.get("dl_speed")
            self._save_path = config.get("save_path")

            # 停止现有任务
            self.stop_service()

            # 启动定时任务 & 立即运行一次
            if self.get_state() or self._onlyonce:
                self.qb = Qbittorrent()
                self.tr = Transmission()
                # 检查配置
                if self._disksize and not StringUtils.is_number(self._disksize):
                    self._disksize = 0
                    logger.error(f"保种体积设置错误：{self._disksize}")
                    self.systemmessage.put(f"保种体积设置错误：{self._disksize}")
                    return
                if self._maxupspeed and not StringUtils.is_number(self._maxupspeed):
                    self._maxupspeed = 0
                    logger.error(f"总上传带宽设置错误：{self._maxupspeed}")
                    self.systemmessage.put(f"总上传带宽设置错误：{self._maxupspeed}")
                    return
                if self._maxdlspeed and not StringUtils.is_number(self._maxdlspeed):
                    self._maxdlspeed = 0
                    logger.error(f"总下载带宽设置错误：{self._maxdlspeed}")
                    self.systemmessage.put(f"总下载带宽设置错误：{self._maxdlspeed}")
                    return
                if self._maxdlcount and not StringUtils.is_number(self._maxdlcount):
                    self._maxdlcount = 0
                    logger.error(f"同时下载任务数设置错误：{self._maxdlcount}")
                    self.systemmessage.put(f"同时下载任务数设置错误：{self._maxdlcount}")
                    return
                if self._size and not StringUtils.is_number(self._size):
                    self._size = 0
                    logger.error(f"种子大小设置错误：{self._size}")
                    self.systemmessage.put(f"种子大小设置错误：{self._size}")
                    return
                if self._seeder and not StringUtils.is_number(self._seeder):
                    self._seeder = 0
                    logger.error(f"做种人数设置错误：{self._seeder}")
                    self.systemmessage.put(f"做种人数设置错误：{self._seeder}")
                    return
                if self._seed_time and not StringUtils.is_number(self._seed_time):
                    self._seed_time = 0
                    logger.error(f"做种时间设置错误：{self._seed_time}")
                    self.systemmessage.put(f"做种时间设置错误：{self._seed_time}")
                    return
                if self._seed_ratio and not StringUtils.is_number(self._seed_ratio):
                    self._seed_ratio = 0
                    logger.error(f"分享率设置错误：{self._seed_ratio}")
                    self.systemmessage.put(f"分享率设置错误：{self._seed_ratio}")
                    return
                if self._seed_size and not StringUtils.is_number(self._seed_size):
                    self._seed_size = 0
                    logger.error(f"上传量设置错误：{self._seed_size}")
                    self.systemmessage.put(f"上传量设置错误：{self._seed_size}")
                    return
                if self._download_time and not StringUtils.is_number(self._download_time):
                    self._download_time = 0
                    logger.error(f"下载超时时间设置错误：{self._download_time}")
                    self.systemmessage.put(f"下载超时时间设置错误：{self._download_time}")
                    return
                if self._seed_avgspeed and not StringUtils.is_number(self._seed_avgspeed):
                    self._seed_avgspeed = 0
                    logger.error(f"平均上传速度设置错误：{self._seed_avgspeed}")
                    self.systemmessage.put(f"平均上传速度设置错误：{self._seed_avgspeed}")
                    return
                if self._seed_inactivetime and not StringUtils.is_number(self._seed_inactivetime):
                    self._seed_inactivetime = 0
                    logger.error(f"未活动时间设置错误：{self._seed_inactivetime}")
                    self.systemmessage.put(f"未活动时间设置错误：{self._seed_inactivetime}")
                    return
                if self._up_speed and not StringUtils.is_number(self._up_speed):
                    self._up_speed = 0
                    logger.error(f"单任务上传限速设置错误：{self._up_speed}")
                    self.systemmessage.put(f"单任务上传限速设置错误：{self._up_speed}")
                    return
                if self._dl_speed and not StringUtils.is_number(self._dl_speed):
                    self._dl_speed = 0
                    logger.error(f"单任务下载限速设置错误：{self._dl_speed}")
                    self.systemmessage.put(f"单任务下载限速设置错误：{self._dl_speed}")
                    return

                # 检查必要条件
                if not self._brushsites or not self._downloader:
                    return

                # 启动任务
                self._scheduler = BackgroundScheduler(timezone=settings.TZ)
                logger.info(f"站点刷流服务启动，周期：{self._cron}分钟")
                try:
                    self._scheduler.add_job(self.brush, 'interval', minutes=self._cron)
                except Exception as e:
                    logger.error(f"站点刷流服务启动失败：{e}")
                    self.systemmessage(f"站点刷流服务启动失败：{e}")
                    return
                if self._onlyonce:
                    logger.info(f"站点刷流服务启动，立即运行一次")
                    self._scheduler.add_job(self.brush, 'date',
                                            run_date=datetime.now(
                                                tz=pytz.timezone(settings.TZ)
                                            ) + timedelta(seconds=3))
                    # 关闭一次性开关
                    self._onlyonce = False
                    self.update_config({
                        "onlyonce": False,
                        "enabled": self._enabled,
                        "notify": self._notify,
                        "brushsites": self._brushsites,
                        "downloader": self._downloader,
                        "disksize": self._disksize,
                        "freeleech": self._freeleech,
                        "maxupspeed": self._maxupspeed,
                        "maxdlspeed": self._maxdlspeed,
                        "maxdlcount": self._maxdlcount,
                        "include": self._include,
                        "exclude": self._exclude,
                        "size": self._size,
                        "seeder": self._seeder,
                        "pubtime": self._pubtime,
                        "seed_time": self._seed_time,
                        "seed_ratio": self._seed_ratio,
                        "seed_size": self._seed_size,
                        "download_time": self._download_time,
                        "seed_avgspeed": self._seed_avgspeed,
                        "seed_inactivetime": self._seed_inactivetime,
                        "up_speed": self._up_speed,
                        "dl_speed": self._dl_speed,
                        "save_path": self._save_path
                    })
                if self._scheduler.get_jobs():
                    # 增加检查任务
                    self._scheduler.add_job(self.check, 'interval', minutes=self._check_interval)
                    # 启动服务
                    self._scheduler.print_jobs()
                    self._scheduler.start()

    def get_state(self) -> bool:
        return True if self._enabled and self._brushsites and self._downloader else False

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 站点的可选项
        site_options = [{"title": site.get("name"), "value": site.get("id")}
                        for site in self.siteshelper.get_indexers()]
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'notify',
                                            'label': '发送通知',
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'chips': True,
                                            'multiple': True,
                                            'model': 'brushsites',
                                            'label': '刷流站点',
                                            'items': site_options
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'downloader',
                                            'label': '下载器',
                                            'items': [
                                                {'title': 'Qbittorrent', 'value': 'qbittorrent'},
                                                {'title': 'Transmission', 'value': 'transmission'}
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'disksize',
                                            'label': '保种体积（GB）',
                                            'placeholder': '达到后停止新增任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'freeleech',
                                            'label': '促销',
                                            'items': [
                                                {'title': '全部（包括普通）', 'value': ''},
                                                {'title': '免费', 'value': 'free'},
                                                {'title': '2X免费', 'value': '2xfree'},
                                            ]
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'maxupspeed',
                                            'label': '总上传带宽（KB/s）',
                                            'placeholder': '达到后停止新增任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'maxdlspeed',
                                            'label': '总下载带宽（KB/s）',
                                            'placeholder': '达到后停止新增任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'maxdlcount',
                                            'label': '同时下载任务数',
                                            'placeholder': '达到后停止新增任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'include',
                                            'label': '包含规则',
                                            'placeholder': '支持正式表达式'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'exclude',
                                            'label': '排除规则',
                                            'placeholder': '支持正式表达式'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'size',
                                            'label': '种子大小（GB）',
                                            'placeholder': '如：5 或 5-10'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'seeder',
                                            'label': '做种人数',
                                            'placeholder': '如：5 或 5-10'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'pubtime',
                                            'label': '发布时间（分钟）',
                                            'placeholder': '如：5 或 5-10'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'seed_time',
                                            'label': '做种时间（小时）',
                                            'placeholder': '达到后删除任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'seed_ratio',
                                            'label': '分享率',
                                            'placeholder': '达到后删除任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'seed_size',
                                            'label': '上传量（GB）',
                                            'placeholder': '达到后删除任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'download_time',
                                            'label': '下载超时时间（小时）',
                                            'placeholder': '达到后删除任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'seed_avgspeed',
                                            'label': '平均上传速度（KB/s）',
                                            'placeholder': '低于时删除任务'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'seed_inactivetime',
                                            'label': '未活动时间（分钟） ',
                                            'placeholder': '超过时删除任务'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'up_speed',
                                            'label': '单任务上传限速（KB/s）',
                                            'placeholder': '种子上传限速'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'dl_speed',
                                            'label': '单任务下载限速（KB/s）',
                                            'placeholder': '种子下载限速'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    "cols": 12,
                                    "md": 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'save_path',
                                            'label': '保存目录',
                                            'placeholder': '留空自动'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "freeleech": "free"
        }

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._event.set()
                    self._scheduler.shutdown()
                    self._event.clear()
                self._scheduler = None
        except Exception as e:
            print(str(e))

    def brush(self):
        """
        执行刷流动作，添加下载任务
        """
        if not self._brushsites or not self._downloader:
            return

        with lock:
            logger.info(f"开始执行刷流任务 ...")
            # 读取种子记录
            task_info = self.get_data("torrents") or {}
            if task_info:
                # 当前保种大小
                torrents_size = sum([task.get("size") or 0 for task in task_info.values()])
            else:
                torrents_size = 0
            # 处理所有站点
            for siteid in self._brushsites:
                siteinfo = self.siteoper.get(siteid)
                if not siteinfo:
                    logger.warn(f"站点不存在：{siteid}")
                    continue
                logger.info(f"开始获取站点 {siteinfo.name} 的新种子 ...")
                torrents = self.searchchain.browse(domain=siteinfo.domain)
                if not torrents:
                    logger.info(f"站点 {siteinfo.name} 没有获取到种子")
                    continue
                # 过滤种子
                for torrent in torrents:
                    # 保种体积（GB） 促销
                    if self._disksize \
                            and (torrents_size + torrent.size) > self._size * 1024**3:
                        logger.warn(f"当前做种体积 {StringUtils.str_filesize(torrents_size)} 已超过保种体积 {self._disksize}，停止新增任务")
                        return
                    # 促销
                    if self._freeleech and torrent.downloadvolumefactor != 0:
                        continue
                    if self._freeleech == "2xfree" and torrent.uploadvolumefactor != 2:
                        continue
                    # 包含规则
                    if self._include and not re.search(r"%s" % self._include, torrent.title, re.I):
                        continue
                    # 排除规则
                    if self._exclude and re.search(r"%s" % self._exclude, torrent.title, re.I):
                        continue
                    # 种子大小（GB）
                    if self._size:
                        sizes = str(self._size).split("-")
                        begin_size = sizes[0]
                        if len(sizes) > 1:
                            end_size = sizes[-1]
                        else:
                            end_size = 0
                        if begin_size and not end_size \
                                and torrent.size > float(begin_size) * 1024**3:
                            continue
                        elif begin_size and end_size \
                                and not float(begin_size) * 1024**3 <= torrent.size <= float(end_size) * 1024**3:
                            continue
                    # 做种人数
                    if self._seeder:
                        seeders = str(self._seeder).split("-")
                        begin_seeder = seeders[0]
                        if len(seeders) > 1:
                            end_seeder = seeders[-1]
                        else:
                            end_seeder = 0
                        if begin_seeder and not end_seeder \
                                and torrent.seeders > int(begin_seeder):
                            continue
                        elif begin_seeder and end_seeder \
                                and not int(begin_seeder) <= torrent.seeders <= int(end_seeder):
                            continue
                    # 计算发布时间
                    pubdate = StringUtils.get_time(pubdate)
                    localtz = pytz.timezone(settings.TZ)
                    localnowtime = datetime.now().astimezone(localtz)
                    localpubdate = pubdate.astimezone(localtz)
                    pudate_minutes = int(localnowtime.timestamp() - localpubdate.timestamp()) / 60
                    # 发布时间（分钟）
                    if self._pubtime:
                        pubtimes = str(self._pubtime).split("-")
                        begin_pubtime = pubtimes[0]
                        if len(pubtimes) > 1:
                            end_pubtime = pubtimes[-1]
                        else:
                            end_pubtime = 0
                        # 将种子发布日志转换为与当前时间的差
                        if begin_pubtime and not end_pubtime \
                                and pudate_minutes > int(begin_pubtime) * 60:
                            continue
                        elif begin_pubtime and end_pubtime \
                                and not int(begin_pubtime) * 60 <= pudate_minutes <= int(end_pubtime) * 60:
                            continue
                    # 同时下载任务数
                    downloads = self.__get_downloading_count(self._downloader)
                    if self._maxdlcount and downloads >= self._maxdlcount:
                        continue
                    # 获取下载器的下载信息
                    downloader_info = self.__get_downloader_info()
                    if downloader_info:
                        current_upload_speed = downloader_info.upload_speed or 0
                        current_download_speed = downloader_info.download_speed or 0
                        # 总上传带宽(KB/s)
                        if self._maxupspeed \
                                and current_upload_speed >= float(self._maxupspeed) * 1024:
                            continue
                        # 总下载带宽(KB/s)
                        if self._maxdlspeed \
                                and current_download_speed >= float(self._maxdlspeed) * 1024:
                            continue
                    # 添加下载任务
                    hash_string = self.__download(torrent=torrent)
                    if not hash_string:
                        logger.warn(f"{torrent.title} 添加刷流任务失败！")
                        continue
                    # 保存任务信息
                    task_info[hash_string] = {
                        "site_name": torrent.site_name,
                        "size": torrent.size
                    }
                    # 发送消息
                    self.__send_add_message(torrent)

            # 保存数据
            self.save_data("torrents", task_info)
            logger.info(f"刷流任务执行完成")

    def check(self):
        """
        定时检查，删除下载任务
        {
            hash: {
                site_name:
                size:
            }
        }
        """
        if not self._downloader:
            return

        with lock:
            logger.info(f"开始检查刷流下载任务 ...")
            # 读取种子记录
            task_info = self.get_data("torrents") or {}
            if not task_info:
                logger.info(f"没有需要检查的刷流下载任务")
                return
            # 种子Hash
            check_hashs = list(task_info.keys())
            logger.info(f"共有 {len(check_hashs)} 个任务正在刷流，开始检查任务状态")
            # 获取下载器实例
            downloader = self.__get_downloader(self._downloader)
            if not downloader:
                return
            # 获取下载器中的种子
            torrents, state = downloader.get_torrents(ids=check_hashs)
            if not state:
                logger.warn("连接下载器出错，将在下个时间周期重试")
                return
            if not torrents:
                logger.warn(f"刷流任务在下载器中不存在，清除记录")
                self.save_data("hashs", {})
                return
            # 检查种子状态，判断是否要删种
            for torrent in torrents:
                site_name = task_info.get(self.__get_hash(torrent, self._downloader)).get("site_name")
                torrent_info = self.__get_torrent_info(self._downloader, torrent)
                # 做种时间（小时）
                if self._seed_time:
                    if torrent_info.get("seeding_time") >= float(self._seed_time) * 3600:
                        logger.info(f"做种时间达到 {self._seed_time} 小时，删除种子：{torrent_info.get('title')}")
                        downloader.delete_torrents(ids=torrent_info.get("hash"), delete_file=True)
                        task_info.pop(torrent_info.get('hash'))
                        self.__send_delete_message(site_name=site_name,
                                                   torrent_title=torrent_info.get("title"),
                                                   reason=f"做种时间达到 {self._seed_time} 小时")
                        continue
                # 分享率
                if self._seed_ratio:
                    if torrent_info.get("ratio") >= float(self._seed_ratio):
                        logger.info(f"分享率达到 {self._seed_ratio}，删除种子：{torrent_info.get('title')}")
                        downloader.delete_torrents(ids=torrent_info.get("hash"), delete_file=True)
                        task_info.pop(torrent_info.get('hash'))
                        self.__send_delete_message(site_name=site_name,
                                                   torrent_title=torrent_info.get("title"),
                                                   reason=f"分享率达到 {self._seed_ratio}")
                        continue
                # 上传量（GB）
                if self._seed_size:
                    if torrent_info.get("uploaded") >= float(self._seed_size) * 1024 * 1024 * 1024:
                        logger.info(f"上传量达到 {self._seed_size} GB，删除种子：{torrent_info.get('title')}")
                        downloader.delete_torrents(ids=torrent_info.get("hash"), delete_file=True)
                        task_info.pop(torrent_info.get('hash'))
                        self.__send_delete_message(site_name=site_name,
                                                   torrent_title=torrent_info.get("title"),
                                                   reason=f"上传量达到 {self._seed_size} GB")
                        continue
                # 下载耗时（小时）
                if self._download_time \
                        and torrent_info.get("downloaded") < torrent_info.get("total_size"):
                    if torrent_info.get("dltime") >= float(self._download_time) * 3600:
                        logger.info(f"下载耗时达到 {self._download_time} 小时，删除种子：{torrent_info.get('title')}")
                        downloader.delete_torrents(ids=torrent_info.get("hash"), delete_file=True)
                        task_info.pop(torrent_info.get('hash'))
                        self.__send_delete_message(site_name=site_name,
                                                   torrent_title=torrent_info.get("title"),
                                                   reason=f"下载耗时达到 {self._download_time} 小时")
                        continue
                # 平均上传速度（KB / s）
                if self._seed_avgspeed:
                    if torrent_info.get("avg_upspeed") <= float(self._seed_avgspeed) * 1024:
                        logger.info(f"平均上传速度低于 {self._seed_avgspeed} KB/s，删除种子：{torrent_info.get('title')}")
                        downloader.delete_torrents(ids=torrent_info.get("hash"), delete_file=True)
                        task_info.pop(torrent_info.get('hash'))
                        self.__send_delete_message(site_name=site_name,
                                                   torrent_title=torrent_info.get("title"),
                                                   reason=f"平均上传速度低于 {self._seed_avgspeed} KB/s")
                        continue
                # 未活动时间（分钟）
                if self._seed_inactivetime:
                    if torrent_info.get("iatime") >= float(self._seed_inactivetime) * 60:
                        logger.info(
                            f"未活动时间达到 {self._seed_inactivetime} 分钟，删除种子：{torrent_info.get('title')}")
                        downloader.delete_torrents(ids=torrent_info.get("hash"), delete_file=True)
                        task_info.pop(torrent_info.get('hash'))
                        self.__send_delete_message(site_name=site_name,
                                                   torrent_title=torrent_info.get("title"),
                                                   reason=f"未活动时间达到 {self._seed_inactivetime} 分钟")
                        continue
            self.save_data("torrents", task_info)
            logger.info(f"刷流下载任务检查完成")

    def __get_downloader(self, dtype: str) -> Optional[Union[Transmission, Qbittorrent]]:
        """
        根据类型返回下载器实例
        """
        if dtype == "qbittorrent":
            return self.qb
        elif dtype == "transmission":
            return self.tr
        else:
            return None

    def __download(self, torrent: TorrentInfo) -> Optional[str]:
        """
        添加下载任务
        """
        if self._downloader == "qbittorrent":
            if not self.qb:
                return None
            # 生成随机Tag
            tag = StringUtils.generate_random_str(10)
            state = self.qb.add_torrent(content=torrent.enclosure,
                                        download_dir=self._save_path or None,
                                        cookie=torrent.site_cookie,
                                        tag=["已整理", "刷流", tag])
            if not state:
                return None
            else:
                # 获取种子Hash
                torrent_hash = self.qb.get_torrent_id_by_tag(tags=tag)
                if not torrent_hash:
                    logger.error(f"{self._downloader} 获取种子Hash失败")
                    return None
            return torrent_hash
        elif self._downloader == "transmission":
            if not self.tr:
                return None
            # 添加任务
            torrent = self.tr.add_torrent(content=torrent.enclosure,
                                          download_dir=self._save_path or None,
                                          cookie=torrent.site_cookie,
                                          labels=["已整理", "刷流"])
            if not torrent:
                return None
            else:
                return torrent.hashString
        return None

    @staticmethod
    def __get_hash(torrent: Any, dl_type: str):
        """
        获取种子hash
        """
        try:
            return torrent.get("hash") if dl_type == "qbittorrent" else torrent.hashString
        except Exception as e:
            print(str(e))
            return ""

    @staticmethod
    def __get_label(torrent: Any, dl_type: str):
        """
        获取种子标签
        """
        try:
            return [str(tag).strip() for tag in torrent.get("tags").split(',')] \
                if dl_type == "qbittorrent" else torrent.labels or []
        except Exception as e:
            print(str(e))
            return []

    @staticmethod
    def __get_torrent_info(downloader_type: str, torrent: Any) -> dict:

        # 当前时间戳
        date_now = int(time.time())
        # QB
        if downloader_type == "qbittorrent":
            # ID
            torrent_id = torrent.get("hash")
            # 标题
            torrent_title = torrent.get("name")
            # 下载时间
            dltime = date_now - torrent.get("added_on") if torrent.get("added_on") else 0
            # 做种时间
            seeding_time = date_now - torrent.get("completion_on") if torrent.get("completion_on") else 0
            # 分享率
            ratio = torrent.get("ratio") or 0
            # 上传量
            uploaded = torrent.get("uploaded") or 0
            # 平均上传速度 Byte/s
            if dltime:
                avg_upspeed = int(uploaded / dltime)
            else:
                avg_upspeed = uploaded
            # 已未活动 秒
            iatime = date_now - torrent.get("last_activity") if torrent.get("last_activity") else 0
            # 下载量
            downloaded = torrent.get("downloaded")
            # 种子大小
            total_size = torrent.get("total_size")
            # 添加时间
            add_time = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(torrent.get("added_on") or 0))
        # TR
        else:
            # ID
            torrent_id = torrent.hashString
            # 标题
            torrent_title = torrent.name
            # 做种时间
            if not torrent.date_done or torrent.date_done.timestamp() < 1:
                seeding_time = 0
            else:
                seeding_time = date_now - int(torrent.date_done.timestamp())
            # 下载耗时
            if not torrent.date_added or torrent.date_added.timestamp() < 1:
                dltime = 0
            else:
                dltime = date_now - int(torrent.date_added.timestamp())
            # 下载量
            downloaded = int(torrent.total_size * torrent.progress / 100)
            # 分享率
            ratio = torrent.ratio or 0
            # 上传量
            uploaded = int(downloaded * torrent.ratio)
            # 平均上传速度
            if dltime:
                avg_upspeed = int(uploaded / dltime)
            else:
                avg_upspeed = uploaded
            # 未活动时间
            if not torrent.date_active or torrent.date_active.timestamp() < 1:
                iatime = 0
            else:
                iatime = date_now - int(torrent.date_active.timestamp())
            # 种子大小
            total_size = torrent.total_size
            # 添加时间
            add_time = time.strftime('%Y-%m-%d %H:%M:%S',
                                     time.localtime(torrent.date_added.timestamp() if torrent.date_added else 0))

        return {
            "hash": torrent_id,
            "title": torrent_title,
            "seeding_time": seeding_time,
            "ratio": ratio,
            "uploaded": uploaded,
            "downloaded": downloaded,
            "avg_upspeed": avg_upspeed,
            "iatime": iatime,
            "dltime": dltime,
            "total_size": total_size,
            "add_time": add_time
        }

    def __send_delete_message(self, site_name: str, torrent_title: str, reason: str):
        """
        发送删除种子的消息
        """
        if self._notify:
            self.chain.post_message(Notification(
                mtype=NotificationType.SiteMessage,
                title=f"【刷流任务删种】",
                text=f"站点：{site_name}\n"
                     f"标题：{torrent_title}\n"
                     f"原因：{reason}"
            ))

    def __send_add_message(self, torrent: TorrentInfo):
        """
        发送添加下载的消息
        """
        msg_text = ""
        if torrent.site_name:
            msg_text = f"站点：{torrent.site_name}"
        if torrent.title:
            msg_text = f"{msg_text}\n标题：{torrent.title}"
        if torrent.size:
            if str(torrent.size).replace(".", "").isdigit():
                size = StringUtils.str_filesize(torrent.size)
            else:
                size = torrent.size
            msg_text = f"{msg_text}\n大小：{size}"
        if torrent.seeders:
            msg_text = f"{msg_text}\n做种数：{torrent.seeders}"
        if torrent.volume_factor:
            msg_text = f"{msg_text}\n促销：{torrent.volume_factor}"
        if torrent.hit_and_run:
            msg_text = f"{msg_text}\nHit&Run：是"

        self.chain.post_message(Notification(
            mtype=NotificationType.SiteMessage,
            title="【刷流任务种子下载】",
            text=msg_text
        ))

    def __get_torrents_size(self) -> int:
        """
        获取任务中的种子总大小
        """
        # 读取种子记录
        task_info = self.get_data("torrents") or {}
        if not task_info:
            return 0
        total_size = sum([task.get("size") or 0 for task in task_info.values()])
        return total_size

    def __get_downloader_info(self) -> schemas.DownloaderInfo:
        """
        获取下载器实时信息
        """
        if self._downloader == "qbittorrent":
            # 调用Qbittorrent API查询实时信息
            info = self.qb.transfer_info()
            return schemas.DownloaderInfo(
                download_speed=info.get("dl_info_speed"),
                upload_speed=info.get("up_info_speed"),
                download_size=info.get("dl_info_data"),
                upload_size=info.get("up_info_data")
            )
        else:
            info = self.tr.transfer_info()
            return schemas.DownloaderInfo(
                download_speed=info.download_speed,
                upload_speed=info.upload_speed,
                download_size=info.current_stats.downloaded_bytes,
                upload_size=info.current_stats.uploaded_bytes
            )

    def __get_downloading_count(self, dltype: str) -> int:
        """
        获取正在下载的任务数量
        """
        downlader = self.__get_downloader(dltype)
        if not downlader:
            return 0
        torrents = downlader.get_downloading_torrents()
        return len(torrents) or 0
