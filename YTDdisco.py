import re
import os
import sys
import time
import stat
import yt_dlp
import shutil
import asyncio
import traceback
import subprocess
from niconico import NicoNico #niconico.py
from typing import Any, Awaitable, Callable, Coroutine, Literal, Optional, Union, Tuple

import discord
from discord.ext import tasks
from discord import app_commands
from discord.ext import commands

import io
import math
import uuid
import functools
from pathlib import Path
from datetime import datetime, timedelta, timezone

import requests
from requests.adapters import HTTPAdapter
from requests_toolbelt import MultipartEncoder, StreamingIterator
from tqdm import tqdm
import concurrent.futures
from urllib3.util.retry import Retry
from os import rename
from subprocess import run
from bs4 import BeautifulSoup
import tempfile


if len(sys.argv) > 3:
    TOKEN = sys.argv[1]
    GUILD_ID = int(sys.argv[2])
    authorized_list = [int(x) for x in sys.argv[3].split(",") if x]
else:
    config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'YTDdisco.config')
    TOKEN = None
    GUILD_ID = None
    authorized_list = []
    if os.path.exists(config_path):
        with open(config_path, encoding='utf-8') as f:
            for line in f:
                if line.strip().startswith('TOKEN='):
                    TOKEN = line.strip().split('=', 1)[1]
                elif line.strip().startswith('GUILD_ID='):
                    GUILD_ID = int(line.strip().split('=', 1)[1])
                elif line.strip().startswith('AUTHORIZED='):
                    authorized_list = [int(x) for x in line.strip().split('=', 1)[1].split(",") if x]
    if TOKEN is None or GUILD_ID is None:
        raise RuntimeError('TOKENまたはGUILD_IDが指定されていません。コマンドライン引数またはYTDdisco.configを用意してください。')

parent_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

# キューシステム用のグローバル変数
download_queue = asyncio.Queue()
is_processing = False
queue_processor_task = None

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix='/', intents=intents)

class Main(commands.Cog):
    def __init__(self, bot: commands.Bot):
        print('login successful')
        self.bot = bot
        self.current_modal = None

    async def start_queue_processor(self):
        """キュープロセッサーを開始する"""
        global queue_processor_task
        if queue_processor_task is None or queue_processor_task.done():
            queue_processor_task = asyncio.create_task(self.process_download_queue())
            print("Queue processor started")

    async def process_download_queue(self):
        """キューからダウンロードタスクを順番に処理する"""
        global is_processing
        while True:
            try:
                # キューから次のタスクを取得
                modal_data = await download_queue.get()

                if modal_data is None:  # 終了シグナル
                    break

                is_processing = True

                # modalのインスタンスを作成して実行
                modal = OptionModal(
                    bot=modal_data['bot'],
                    zipfile=modal_data['zipfile'],
                    codec=modal_data['codec'],
                    extension=modal_data['extension'],
                    resolution=modal_data['resolution'],
                    thumbnail=modal_data['thumbnail'],
                    metadata=modal_data['metadata'],
                    options=modal_data['options'],
                    txt_content=modal_data['txt_content']
                )
                self.current_modal = modal

                try:
                    await modal.main_without_interaction(
                        user=modal_data['user'],
                        channel=modal_data['channel'],
                        guild=modal_data['guild']
                    )
                except Exception as e:
                    print(f"ダウンロード処理中にエラーが発生しました: {e}")
                    traceback.print_exc()
                finally:
                    self.current_modal = None
                    is_processing = False
                    download_queue.task_done()

            except Exception as e:
                print(f"キュープロセッサーでエラーが発生しました: {e}")
                is_processing = False

    async def add_to_queue(self, modal_data):
        """ダウンロードタスクをキューに追加"""
        queue_size = download_queue.qsize()
        await download_queue.put(modal_data)
        return queue_size + 1  # キューの位置を返す

    @app_commands.command(name = 'dl', description = '動画ダウンロード')
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe(
        txtfile='URLが記載されたTXTファイル（任意）',
        zipfile='ファイルをZIPにまとめるかどうか',
        extension='出力フォーマット',
        codec='動画コーデック',
        resolution='解像度',
        thumbnail='サムネイルを含めるかどうか',
        metadata='メタデータを含めるかどうか',
        options='追加オプション'
    )
    async def dl(self,
        interaction: discord.Interaction,
        txtfile: Optional[discord.Attachment] = None,
        zipfile : Optional[bool] = True,
        extension: Literal['mp4','mp3','m4a','wav','flac'] = 'mp3',
        codec: Literal['default', 'h264', 'h265', 'vp9', 'av1'] = 'default',
        resolution: Literal['worst', '144', '240', '360', '480', '720', '1080', '1440', '2160', 'best'] = 'best',
        thumbnail: Optional[bool] = True,
        metadata: Optional[bool] = True,
        options: Optional[str] = '',
        ) -> Callable[[discord.Interaction], Awaitable[None]]:

        await self.bot.change_presence(activity=discord.Game(name='Download'))

        # TXTファイルが添付されている場合は、modalを表示せずに直接実行
        if txtfile is not None:
            # TXTファイルの内容を確認
            if not txtfile.filename.endswith('.txt'):
                embed = discord.Embed(
                    description = 'アップロードされたファイルがTXTファイルではありません。',
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            # TXTファイルの内容を読み取り
            try:
                file_content = await txtfile.read()
                url_content = file_content.decode('utf-8')
                # レスポンスを遅延
                await interaction.response.defer()
                # キューに追加
                modal_data = {
                    'bot': self.bot,
                    'zipfile': zipfile,
                    'codec': codec,
                    'extension': extension,
                    'resolution': resolution,
                    'thumbnail': thumbnail,
                    'metadata': metadata,
                    'options': options,
                    'txt_content': url_content,
                    'user': interaction.user,
                    'channel': interaction.channel,
                    'guild': interaction.guild
                }
                queue_position = await self.add_to_queue(modal_data)

                if is_processing:
                    embed = discord.Embed(
                        description=f'ダウンロードキューに追加されました。順番: {queue_position}番目\n現在処理中のタスクが完了次第開始されます。',
                        color=discord.Color.blue()
                    )
                    # 応答を遅延
                    await interaction.response.defer()
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    embed = discord.Embed(
                        description='ダウンロードを開始します...',
                        color=discord.Color.green()
                    )
                    # 応答を遅延
                    await interaction.response.defer()
                    await interaction.followup.send(embed=embed, ephemeral=True)

            except Exception as e:
                embed = discord.Embed(
                    description = f'TXTファイルの読み込み中にエラーが発生しました: {str(e)}',
                    color=discord.Color.red()
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
        else:
            # TXTファイルが添付されていない場合は通常のmodal表示
            modal = OptionModal(bot=self.bot, zipfile=zipfile, codec=codec, extension=extension, resolution=resolution, thumbnail=thumbnail, metadata=metadata, options=options, txt_content=None)

            # modalにqueueシステムへの参照を渡す
            modal.get_command_cog = self

            await interaction.response.send_modal(modal)

    @app_commands.command(name = 'progress', description = '進捗を再送信')
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe()
    async def progress_send(self, interaction: discord.Interaction,) -> Callable[[discord.Interaction], Awaitable[None]]:
        if self.current_modal:
            await self.current_modal.progress_send(interaction)
        else:
            embed = discord.Embed(
                description = '現在実行中のダウンロードタスクがありません。',
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name = 'stop', description = 'ダウンロードを停止(botの再起動)')
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe()
    async def stop_download(self, interaction: discord.Interaction,) -> Callable[[discord.Interaction], Awaitable[None]]:
        embed = discord.Embed(
            description = f'{interaction.user.display_name} によりダウンロードが停止されました。',
            color=discord.Color.dark_red()
        )
        await interaction.response.send_message(embed=embed)

        python = sys.executable
        os.execl(python, python, *sys.argv)

class OptionModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, zipfile: bool = True, extension: str = 'mp3', codec: str = 'default', resolution: str = 'best', thumbnail: bool = True, metadata: bool = True, options: str = '', txt_content: str = None) -> None:
        super().__init__(title='Input Download URL', timeout=None)
        self.zipfile = zipfile; self.extension = extension; self.codec = codec ;self.resolution = resolution; self.thumbnail = thumbnail; self.metadata = metadata; self.options = options.split(',')
        self.txt_content = txt_content

        '''
        limit: ダウンロード数制限を解除
        nvidia: GPUでエンコード
        dm: DMで実行
        local: ローカルに保存
        '''

        self.bot = bot
        self.aria2_sites = ['nicovideo',]
        self.streamlink_sites = ['abema.tv']
        self.max_downloads = 2000
        self.nvidia = False

        if 'limit' in options:
            self.max_downloads = 2*32

        if 'nvidia' in self.options:
            self.nvidia = True

        # TXTファイルから内容が渡された場合はTextInputを作成しない
        if self.txt_content is None:
            self.url_input: discord.ui.TextInput = discord.ui.TextInput(
                label='URL(複数個の場合は改行して入力してください)',
                style=discord.TextStyle.paragraph,
                placeholder='url',
                default='',
                required=True,
            )
            self.add_item(self.url_input)
        else:
            # TXTファイルからの内容をMockオブジェクトとして設定
            self.url_input = type('MockInput', (), {'value': self.txt_content})()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        modal_data = {
            'bot': self.bot,
            'zipfile': self.zipfile,
            'codec': self.codec,
            'extension': self.extension,
            'resolution': self.resolution,
            'thumbnail': self.thumbnail,
            'metadata': self.metadata,
            'options': ','.join(self.options),
            'txt_content': self.url_input.value,
            'user': interaction.user,
            'channel': interaction.channel,
            'guild': interaction.guild
        }

        queue_position = await self.get_command_cog.add_to_queue(modal_data)

        if is_processing:
            embed = discord.Embed(
                description=f'ダウンロードキューに追加されました。順番: {queue_position}番目\n現在処理中のタスクが完了次第開始されます。',
                color=discord.Color.blue()
            )
            await interaction.response.send_message(embed=embed)
        else:
            embed = discord.Embed(
                description='ダウンロードを開始します...',
                color=discord.Color.green()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def main(self, interaction: discord.Interaction) -> Callable[[discord.Interaction], Awaitable[None]]:
        # TXTファイルから直接呼び出された場合の初期化処理
        if not hasattr(self, 'run'):
            self.run = True
            self.time = time.time()
            self.embed_color = discord.Color.dark_theme()
            self.progress_content = ''
            self.author_name = interaction.user.display_name
            self.author_url = interaction.user.avatar.url
            self.author = interaction.user
    
        # 同時に1つしか実行できないようにする（キューシステムで管理）
        if ('dm' in self.options) and (interaction.user.id not in authorized_list):
            now = datetime.now(timezone(timedelta(hours=9)))
            unix_timestamp = int(datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=now.tzinfo).timestamp())
            today = int(datetime.now().strftime('%Y%m%d'))
            password = ((unix_timestamp+1)*today % 982451653)%10000
            if str(password) not in self.options:
                self.options = []

        self.status_content = '[loading url]'

        # インタラクションが既にレスポンス済みでない場合のみdeferする
        try:
            if not interaction.response.is_done():
                await interaction.response.defer()
        except discord.errors.HTTPException as e:
            if e.code != 40060:  # 40060 = Interaction has already been acknowledged
                raise
            # 既に確認済みの場合は無視
            pass

        self.channel = self.bot.get_channel(interaction.channel.id)

        embed = discord.Embed(
            title = '[initializing]',
            description = '',
            color = self.embed_color,
            )
        embed.set_author(name=self.author_name, icon_url=self.author_url)

        if 'dm' in self.options:
            self.msg = await self.author.send(embed=embed,file=None)
        else:
            # レスポンス済みの場合はfollowupを使用、そうでなければ通常のチャンネル送信
            try:
                if interaction.response.is_done():
                    self.msg = await interaction.followup.send(embed=embed, wait=True)
                else:
                    self.msg = await interaction.channel.send(embed=embed,file=None)
            except discord.errors.HTTPException as e:
                # インタラクションに問題がある場合は通常のチャンネル送信にフォールバック
                self.msg = await self.channel.send(embed=embed,file=None)
        self.edit_message.start()

        # self.msg = await interaction.followup.send(content='[initializing]', wait=True, ephemeral=self.ephemeral)

        if 'local' in self.options:
            temp_path = 'H:/'
        else:
            temp_path = os.path.join(tempfile.gettempdir(), "YTD_temp")
            self.delete_folder(temp_path)

            uploads_dir = os.path.join(temp_path, 'uploads')
            os.makedirs(uploads_dir)

        self.input_url_list = self.url_input.value.split()
        url_list, self.num = self.get_urllist(self.input_url_list)

        if self.num > self.max_downloads:
            embed = discord.Embed(
                description = f'一度にダウンロードできる最大ファイル数は {self.max_downloads} です。',
                )
            await self.channel.send(embed=embed)

            self.run = False
            self.edit_message.stop()
            return

        if len(url_list) == 0:
            #await self.msg.edit(content='Invalid url error')
            return
        print(url_list)

        self.cnt = 1
        for item in url_list:
                self.status_content = '[downloading]'
                self.embed_color = discord.Color.brand_red()
                if type(item) is tuple:
                    downloads_dir = os.path.join(temp_path, item[1])
                    for url in item[0]:
                        try:
                            await asyncio.to_thread(self.download, downloads_dir, url, self.extension, self.resolution, self.thumbnail, self.metadata,)
                        except Exception:
                            traceback.print_exc()
                        self.cnt += 1
                    self.cnt -= 1
                    self.status_content = '[making zip]'
                    self.embed_color = discord.Color.yellow()

                    if self.zipfile == False:
                        self.status_content = f'[uploading] {self.cnt}/{self.num} : {item[1]}'
                        self.embed_color = discord.Color.teal()
                        await self.upload_file(downloads_dir, item)
                    else:
                        shutil.move(downloads_dir, uploads_dir)
                    self.delete_folder(downloads_dir)
                    self.cnt += 1

                elif type(item) is str:
                    downloads_dir = os.path.join(temp_path, 'downloads')
                    try:
                        await asyncio.to_thread(self.download, downloads_dir, item, self.extension, self.resolution, self.thumbnail, self.metadata)
                    except Exception as e:
                        print(e)
                    download_path = os.path.join(downloads_dir, os.listdir(downloads_dir)[0])

                    if self.zipfile == False:
                        self.status_content = f'[uploading] {self.cnt}/{self.num} : {os.listdir(downloads_dir)[0]}'
                        self.embed_color = discord.Color.teal()
                        # self.progress_content = ''
                        await self.upload_file(download_path, item)
                    else:
                        if 'local' not in self.options:
                            shutil.move(download_path, uploads_dir)


                    self.cnt += 1
                    if 'local' not in self.options:
                        self.delete_folder(downloads_dir)

        if self.zipfile == True:
            self.num = 1; self.cnt = 1
            self.status_content = '[making zip]'
            self.embed_color = discord.Color.yellow()
            await asyncio.to_thread(shutil.make_archive, uploads_dir, 'zip', uploads_dir)
            self.status_content = '[uploading] 1/1'
            self.embed_color = discord.Color.teal()
            uploadzip_dir = os.path.join(temp_path, 'uploads.zip')
            await self.upload_file(uploadzip_dir, self.input_url_list)

        self.delete_folder(temp_path)
        self.status_content = '[finished]'
        self.embed_color = discord.Color.brand_green()

        await asyncio.sleep(1)

        self.run = False
        self.edit_message.stop()
        #await self.msg.delete()
        print('finished')
        await self.bot.change_presence(activity=discord.Game(name=''))

    async def main_without_interaction(self, user, channel, guild):
        """インタラクションを使わずにダウンロード処理を実行"""
        # 初期化処理
        self.run = True
        self.time = time.time()
        self.embed_color = discord.Color.dark_theme()
        self.progress_content = ''
        self.author_name = user.display_name
        self.author_url = user.avatar.url if user.avatar else user.default_avatar.url
        self.author = user

        # パスワードチェック（必要に応じて）
        if ('dm' in self.options) and (user.id not in authorized_list):
            now = datetime.now(timezone(timedelta(hours=9)))
            unix_timestamp = int(datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=now.tzinfo).timestamp())
            today = int(datetime.now().strftime('%Y%m%d'))
            password = ((unix_timestamp+1)*today % 982451653)%10000
            if str(password) not in self.options:
                self.options = []

        self.status_content = '[loading url]'
        self.channel = channel

        embed = discord.Embed(
            title = '[initializing]',
            description = '',
            color = self.embed_color,
        )
        embed.set_author(name=self.author_name, icon_url=self.author_url)

        if 'dm' in self.options:
            self.msg = await self.author.send(embed=embed,file=None)
        else:
            self.msg = await channel.send(embed=embed,file=None)

        self.edit_message.start()

        if 'local' in self.options:
            temp_path = 'H:/'
        else:
            temp_path = os.path.join(tempfile.gettempdir(), "YTD_temp")
            self.delete_folder(temp_path)

            uploads_dir = os.path.join(temp_path, 'uploads')
            os.makedirs(uploads_dir)

        self.input_url_list = self.url_input.value.split()
        url_list, self.num = self.get_urllist(self.input_url_list)

        if self.num > self.max_downloads:
            embed = discord.Embed(
                description = f'一度にダウンロードできる最大ファイル数は {self.max_downloads} です。',
            )
            await self.channel.send(embed=embed)

            self.run = False
            self.edit_message.stop()
            return

        if len(url_list) == 0:
            # 無効なURLエラー
            return
        print(url_list)

        self.cnt = 1
        for item in url_list:
                self.status_content = '[downloading]'
                self.embed_color = discord.Color.brand_red()
                if type(item) is tuple:
                    downloads_dir = os.path.join(temp_path, item[1])
                    for url in item[0]:
                        try:
                            await asyncio.to_thread(self.download, downloads_dir, url, self.extension, self.resolution, self.thumbnail, self.metadata,)
                        except Exception:
                            traceback.print_exc()
                        self.cnt += 1
                    self.cnt -= 1
                    self.status_content = '[making zip]'
                    self.embed_color = discord.Color.yellow()

                    if self.zipfile == False:
                        self.status_content = f'[uploading] {self.cnt}/{self.num} : {item[1]}'
                        self.embed_color = discord.Color.teal()
                        await self.upload_file(downloads_dir, item)
                    else:
                        shutil.move(downloads_dir, uploads_dir)
                    self.delete_folder(downloads_dir)
                    self.cnt += 1

                elif type(item) is str:
                    downloads_dir = os.path.join(temp_path, 'downloads')
                    try:
                        await asyncio.to_thread(self.download, downloads_dir, item, self.extension, self.resolution, self.thumbnail, self.metadata)
                    except Exception as e:
                        print(e)
                    download_path = os.path.join(downloads_dir, os.listdir(downloads_dir)[0])

                    if self.zipfile == False:
                        self.status_content = f'[uploading] {self.cnt}/{self.num} : {os.listdir(downloads_dir)[0]}'
                        self.embed_color = discord.Color.teal()

                        await self.upload_file(download_path, item)
                    else:
                        if 'local' not in self.options:
                            shutil.move(download_path, uploads_dir)


                    self.cnt += 1
                    if 'local' not in self.options:
                        self.delete_folder(downloads_dir)

        if self.zipfile == True:
            self.num = 1; self.cnt = 1
            self.status_content = '[making zip]'
            self.embed_color = discord.Color.yellow()
            await asyncio.to_thread(shutil.make_archive, uploads_dir, 'zip', uploads_dir)
            self.status_content = '[uploading] 1/1'
            self.embed_color = discord.Color.teal()
            uploadzip_dir = os.path.join(temp_path, 'uploads.zip')
            await self.upload_file(uploadzip_dir, self.input_url_list)

        self.delete_folder(temp_path)
        self.status_content = '[finished]'
        self.embed_color = discord.Color.brand_green()

        await asyncio.sleep(1)

        self.run = False
        self.edit_message.stop()
        print('finished')
        await self.bot.change_presence(activity=discord.Game(name=''))

    def delete_folder(self, folder: str) -> None:
        if os.path.isdir(folder):
            try:
                shutil.rmtree(folder, onerror=self.onerror)
            except Exception as e:
                print(f"{folder} の削除に失敗しました: {e}")
                # 代替削除方法を試行
                self.force_delete_folder(folder)

    def onerror(self, func, path, exc_info):
        """Handle errors during shutil.rmtree operations"""
        try:
            # ファイル・ディレクトリの読み取り専用属性を解除
            if os.path.isfile(path):
                os.chmod(path, stat.S_IWRITE)
            elif os.path.isdir(path):
                os.chmod(path, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)

            # 再試行
            func(path)
        except Exception as e:
            print(f"Error in onerror for {path}: {e}")
            # If still failing, try alternative method
            self.force_delete_path(path)

    def force_delete_folder(self, folder: str) -> None:
        """Force delete folder using alternative methods"""
        if not os.path.exists(folder):
            return

        try:
            # Windows用にrmdir /s を使う
            subprocess.run(['rmdir', '/s', '/q', folder], shell=True, check=True)
            print(f"rmdirで {folder} を削除しました")
        except Exception as e:
            print(f"rmdir失敗: {e}")
            # 手動で再帰削除を試行
            self.manual_delete_folder(folder)

    def force_delete_path(self, path: str) -> None:
        """Force delete a single file or directory"""
        try:
            if os.path.isfile(path):
                os.chmod(path, stat.S_IWRITE)
                os.remove(path)
            elif os.path.isdir(path):
                os.chmod(path, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
                os.rmdir(path)
        except Exception as e:
            print(f"{path} の削除に失敗: {e}")

    def manual_delete_folder(self, folder: str) -> None:
        """Manually delete folder contents recursively"""
        try:
            for root, dirs, files in os.walk(folder, topdown=False):
                # すべてのファイルを削除
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        os.chmod(file_path, stat.S_IWRITE)
                        os.remove(file_path)
                    except Exception as e:
                        print(f"ファイル {file_path} の削除に失敗: {e}")

                # すべてのディレクトリを削除
                for dir in dirs:
                    dir_path = os.path.join(root, dir)
                    try:
                        os.chmod(dir_path, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
                        os.rmdir(dir_path)
                    except Exception as e:
                        print(f"ディレクトリ {dir_path} の削除に失敗: {e}")

            # 最後にルートフォルダを削除
            try:
                os.chmod(folder, stat.S_IWRITE | stat.S_IEXEC | stat.S_IREAD)
                os.rmdir(folder)
                print(f"Successfully deleted {folder} manually")
            except Exception as e:
                print(f"Could not delete root folder {folder}: {e}")
        except Exception as e:
            print(f"Manual deletion failed: {e}")

    @tasks.loop(seconds=10)
    async def edit_message(self):
        t = int(time.time() - self.time)
        embed = discord.Embed(
            title = self.status_content,
            description = f'{self.progress_content} ({str(t//3600).zfill(2)}:{str((t%3600)//60).zfill(2)}:{str(t%3600%60).zfill(2)})',
            color = self.embed_color,
            )
        embed.set_author(name=self.author_name, icon_url=self.author_url)
        try:
            await self.msg.edit(embed=embed)
        except:pass

    async def progress_send(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            description = 'This string has no meaning :-)'
        )
        await interaction.response.send_message(embed=embed, delete_after=0.1, ephemeral=True)

        if self.run:
            t = int(time.time() - self.time)
            embed = discord.Embed(
                title = self.status_content,
                description = f'{self.progress_content} ({str(t//3600).zfill(2)}:{str((t%3600)//60).zfill(2)}:{str(t%3600%60).zfill(2)})',
                color = self.embed_color,
                )
            embed.set_author(name=self.author_name, icon_url=self.author_url)
        else:
            embed = discord.Embed(description='There are no processes currently running.')

        self.msg = await interaction.channel.send(embed=embed,file=None)

    async def upload_file(self, path: str, message: str) -> None:
        print('uploading now')

        gigafile_url = await self.upload_to_gigafile(path)

        # Truncate the message if it exceeds Discord's character limit (2000 characters)
        if len(message) > 2000:
            message = message[:1997] + '...'

        if 'dm' in self.options:
            await self.author.send(content=f'<{gigafile_url}>\n{message}')
        else:
            await self.channel.send(content=f'<{gigafile_url}>\n{message}')

        os.remove(path)

    async def upload_to_gigafile(self, path: str) -> str:
        gigafile = Giga(self, path)

        await asyncio.to_thread(gigafile.upload)

        return gigafile.get_download_page()

    def extract_url(self, url: str, state='') -> tuple[list,str]:
        url_temp_list = []

        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'force_generic_extractor': True,
        }

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            video_urls = [entry['url'] for entry in info_dict['entries']]

            if state == 'playlist':
                name:str = info_dict.get('title')
            elif state == 'channel':
                name:str = info_dict.get('channel')

        for url in video_urls:
            url_temp_list.append(url)

        return url_temp_list, name

    def get_urllist(self, input_url_list: list[str]) -> Tuple[list[str|tuple], int]:
        url_list = []
        cnt = 0

        for url in input_url_list:
            self.status_content = f'[loading url] {cnt+1}/{len(url_list)}'
            url_temp_list = []
            try:
                if re.fullmatch(r'sm\d+', url):
                    url = f'https://www.nicovideo.jp/watch/{url}'
                elif (
                    not url.startswith('https://')
                    and len(url) == 11
                    and re.fullmatch(r'[A-Za-z0-9_-]{11}', url)
                ):
                    url = f'https://www.youtube.com/watch?v={url}'
                if 'https://' in url:
                    if 'youtube' in url:
                        if '@' in url:
                            url_list.append(self.extract_url(url, state='channel'))
                            cnt += len(url_list[-1][0])
                        else:
                            if 'm.' in url:
                                url = url.replace('m.','www.')
                            if 'shorts' in url:
                                url = 'https://www.youtube.com/watch?v='+url[31:]
                            if '&t=' in url:
                                url = url[:43]
                            if '&list=' in url:
                                url = url[:43]
                            if '&pp=' in url:
                                url = url[:43]
                            if len(url) == 43:
                                url_list.append(url)
                                cnt += 1
                            elif 'playlist' in url:
                                url_list.append(self.extract_url(url, state='playlist'))
                                cnt += len(url_list[-1][0])

                    elif 'youtu.be' in url:
                        url = url[:28]
                        url = 'https://www.youtube.com/watch?v=' + url[17:]
                        url_list.append(url)
                        cnt += 1

                    elif 'soundcloud.com' in url:
                        if 'on.soundcloud.com' in url:
                            res = requests.get(url)
                            url = res.url

                        ydl_opts = {
                            'quiet': True,
                            'extract_flat': True,
                            'force_generic_extractor': True,
                        }

                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            info_dict = ydl.extract_info(url, download=False)

                        if info_dict.get('_type') == 'playlist':
                            url += '/tracks'
                            url_temp_list, name = self.extract_url(url, state='playlist')

                            if name[-8:] == '(Tracks)':
                                name = name[:-9]

                            url_list.append((url_temp_list, name))
                            cnt += len(url_list[-1][0])

                        elif info_dict.get('_type') == None:
                            url_list.append(url)
                            cnt += 1

                        else:
                            print(info_dict.get('_type'))
                            print('exceptional error')

                    elif 'https://www.nicovideo.jp/' in url and 'mylist' in url:
                        client = NicoNico()
                        for mylist in client.video.get_mylist(url):
                            for line in mylist.items:
                                url_temp_list.append(line.video.url)
                            url_list.append((url_temp_list, mylist.name))
                            cnt += len(url_list[-1][0])

                    else:
                        url_list.append(url)
                        cnt += 1
            except:
                print('url loading failed')

        return url_list, cnt

    def download(self, path: str , url: str, extension: str, resolution: str, thumbnail: str, metadata: str) -> None:
        self.status_content = f'[downloading] {self.cnt}/{self.num}'

        if 'gigafile.nu' in url:
            gigafile = Giga(self, url)
            dl_path = gigafile.download(path)

            if os.path.splitext(dl_path)[-1] == '.zip':
                shutil.unpack_archive(dl_path, path)
                os.remove(dl_path)
                time.sleep(20)

        elif not any(sub in url for sub in self.streamlink_sites):
            if extension == 'mp4':
                options = {
                    'writethumbnail': thumbnail,
                    'outtmpl': os.path.join(path, '%(title)s.%(ext)s'),
                    'format': f'bv*[ext={extension}]+ba[ext=m4a]/b[ext={extension}]' if resolution == 'best' else f'wv*[ext={extension}]+wa[ext=m4a]/w[ext={extension}]' if resolution == 'worst' else f'bv[ext={extension}][height<={resolution}]+ba[ext=m4a]/best',
                    'http_headers': {'Accept-Language': 'ja-JP'},
                    'progress_hooks': [self.my_hook],
                    'live_from_start': True,
                    'postprocessors': [
                        {'key': 'FFmpegMetadata',
                        'add_metadata': metadata},
                        {'key': 'EmbedThumbnail'}
                        ],
                }

                if self.codec  == 'h264':
                    if self.nvidia:
                        options.update({'postprocessor_args': ['-c:v', 'h264_nvenc']})
                    else:
                        options.update({'postprocessor_args': ['-c:v', 'libx264']})
                elif self.codec == 'h265':
                    if self.nvidia:
                        options.update({'postprocessor_args': ['-c:v', 'hevc_nvenc']})
                    else:
                        options.update({'postprocessor_args': ['-c:v', 'libx265']})
                elif self.codec == 'vp9':
                    options.update({'postprocessor_args': ['-c:v', 'libvpx-vp9']})
                elif self.codec == 'av1':
                    options.update({'postprocessor_args': ['-c:v', 'libaom-av1']})

            else:
                options = {
                    'writethumbnail': False if extension == 'wav' else thumbnail,
                    'outtmpl': os.path.join(path, '%(title)s.%(ext)s'),
                    'format': 'bestaudio/best',
                    'http_headers': {'Accept-Language': 'ja-JP'},
                    'progress_hooks': [self.my_hook],
                    'trim-filenames': 'LENGTH',
                    'live_from_start': True,
                    'postprocessors': [
                        {'key': 'FFmpegExtractAudio',
                        'preferredcodec': extension,
                        'preferredquality': '192'},
                        {'key': 'FFmpegMetadata',
                        'add_metadata': metadata},
                        {'key': 'EmbedThumbnail'}
                        ]
                }

            if any(sub in url for sub in self.aria2_sites):
                options.update({
                    'external_downloader': 'aria2c',
                    'external_downloader_args': ['-x 16', '-k 1M', '-c', '-n'],
                })

            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download(url)

        else:
            title = self.get_video_title(url)
            self.status_content = f'[downloading] {self.cnt}/{self.num} : {title}'

            subprocess.run(
                [
                    'streamlink',
                    url,
                    '1080p' if resolution == 'best' else '360p' if resolution == 'worst' else resolution+'p',
                    '-o',
                    os.path.join(path, f'{title}.ts'),
                ]
            )

            subprocess.run(
                [
                    'ffmpeg',
                    '-i',
                    os.path.join(path, f'{title}.ts'),
                    '-c',
                    'copy',
                    os.path.join(path, f'{title}.mp4')
                ]
            )

            os.remove(os.path.join(path, f'{title}.ts'))

        #self.progress_content = ''

    def my_hook(self, d: dict):
        try:
            title = os.path.basename(d['filename'])
            percent = self.remove_color_codes(d['_percent_str'])
            downloaded_bytes = d['downloaded_bytes']
            downloaded_Mbytes = round((downloaded_bytes/1048576),2)
            speed = float(round((float(d['speed'])/1048576),2))
            eta = self.remove_color_codes(d['_eta_str'])
            try:
                total_bytes = d['total_bytes']
            except:
                try:
                    total_bytes = d['total_bytes_estimate']
                except:
                    total_bytes = 0
            total_Mbytes = round((total_bytes/1048576),2)

            self.status_content = f'[downloading] {self.cnt}/{self.num} : {title}'
            self.progress_content = f'{percent} of {downloaded_Mbytes}/{total_Mbytes} MiB at  {speed}MiB/s  ETA {eta}'

        except Exception as e:
            self.status_content = f'[downloading] {self.cnt}/{self.num}'

    def remove_color_codes(self, input_string: str) -> str:
        color_pattern = re.compile(r'\x1b\[[0-9;]*m')
        return color_pattern.sub('', input_string)

    def get_video_title(self, video_url: str) -> str:
        ydl_opts = {
            'outtmpl': '%(title)s',
            'skip_download': True,
            'quiet': True,
            'no_warnings': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(video_url, download=False)
            video_title = info_dict.get('title', None)
        return video_title


class Giga:
    def __init__(self, modal: OptionModal, path) -> None:
        self.modal = modal
        self.uri = path
        self.chunk_size = 1024*1024*10
        self.chunk_copy_size = 1024*1024
        self.thread_num = 1
        self.progress = True
        self.data = None
        self.pbar = None
        self.current_chunk = 0
        self.aria2 = False
        self.total_uploaded = 0
        self.session = self.requests_retry_session()
        self.session.request = functools.partial(self.session.request, timeout=10)

    def bytes_to_size_str(self, bytes):
        if bytes == 0:
            return '0B'
        units = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
        i = int(math.floor(math.log(bytes, 1024)))
        p = math.pow(1024, i)
        return f'{bytes/p:.02f} {units[i]}'

    def requests_retry_session(self,
        retries=5,
        backoff_factor=0.2,
        status_forcelist=None, # (500, 502, 504)
        session=None,
    ):
        session = session or requests.Session()
        retry = Retry(
            total=retries,
            backoff_factor=backoff_factor,
            status_forcelist=status_forcelist,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        return session


    def split_file(self, input_file, out, target_size=None, start=0, chunk_copy_size=1024*1024):
        input_file = Path(input_file)
        size = 0

        input_size = input_file.stat().st_size
        if target_size is None:
            output_size = input_size - start
        else:
            output_size = min( target_size, input_size - start)

        with open(input_file, 'rb') as f:
            f.seek(start)
            while True:
                # print(f'{size / output_size * 100:.2f}%', end='\r')
                if size == output_size: break
                if size > output_size:
                    raise Exception(f'Size ({size}) is larger than {target_size} bytes!')
                current_chunk_size = min(chunk_copy_size, output_size - size)
                chunk = f.read(current_chunk_size)
                if not chunk: break
                size += len(chunk)
                out.write(chunk)


    def upload_chunk(self, chunk_no, chunks):
        self.chunk_no = chunk_no
        self.bar = self.pbar[self.chunk_no % self.thread_num] if self.pbar else None
        with io.BytesIO() as f:
            self.split_file(self.uri, f, self.chunk_size, start=self.chunk_no * self.chunk_size, chunk_copy_size=self.chunk_copy_size)
            chunk_size = f.tell()
            f.seek(0)
            fields = {
                'id': self.token,
                'name': Path(self.uri).name,
                'chunk': str(self.chunk_no),
                'chunks': str(chunks),
                'lifetime': '100',
                'file': ('blob', f, 'application/octet-stream'),
            }
            form_data = MultipartEncoder(fields)
            headers = {
                'content-type': form_data.content_type,
            }
            self.form_data_binary = form_data.to_string()
            del form_data

        self.size = len(self.form_data_binary)
        if self.bar:
            self.bar.desc = f'chunk {chunk_no + 1}/{chunks}'
            self.bar.reset(total=self.size)
            # bar.refresh()

        while True:
            try:
                streamer = StreamingIterator(self.size, self.gen())
                resp = self.session.post(f'https://{self.server}/upload_chunk.php', data=streamer, headers=headers)
            except Exception as e:
                print(e)
                print('Retrying...')
            else:
                break

        resp_data = resp.json()
        self.current_chunk += 1

        if 'url' in resp_data:
            self.data = resp_data
        if 'status' not in resp_data or resp_data['status']:
            print(resp_data)
            self.failed = True

    def gen(self):
        offset = 0
        total_size = os.path.getsize(self.uri)  # chunk_sizesは各チャンクのサイズのリスト

        while True:
            if offset < self.size:
                update_tick = 1024 * 128
                yield self.form_data_binary[offset:offset+update_tick]
                if self.bar:
                    self.total_uploaded += min(update_tick, self.size - offset)
                    percent = round(self.total_uploaded / total_size * 100, 1)
                    uploaded_size = round((self.total_uploaded / 1048576), 2)
                    total_size_MB = round((total_size / 1048576), 2)
                    speed = 0 if self.bar.format_dict['rate'] == None else self.bar.format_dict['rate']
                    speed_MB = round((speed / 1048576), 2)
                    eta = timedelta(seconds=round((total_size - self.total_uploaded) / speed if speed and total_size else 0))
                    self.modal.progress_content = f'{percent}% of {uploaded_size}/{total_size_MB} MiB at  {speed_MB}MiB/s  ETA {eta}'

                    self.bar.update(min(update_tick, self.size - offset))
                    self.bar.refresh()
                offset += update_tick
            else:
                if self.chunk_no != self.current_chunk:
                    time.sleep(0.01)
                else:
                    time.sleep(0.1)
                    break

    def upload(self):
        self.token = uuid.uuid1().hex
        self.pbar = None
        self.failed = False
        assert Path(self.uri).exists()
        size = Path(self.uri).stat().st_size
        chunks = math.ceil(size / self.chunk_size)
        print(f'Filesize {self.bytes_to_size_str(size)}, chunk size: {self.bytes_to_size_str(self.chunk_size)}, total chunks: {chunks}')

        if self.progress:
            self.pbar = []
            for i in range(self.thread_num):
                self.pbar.append(tqdm(total=size, unit='B', unit_scale=True, leave=False, unit_divisor=1024, ncols=100, position=i))

        self.server = re.search(r'var server = "(.+?)"', self.session.get('https://gigafile.nu/').text)[1]

        self.upload_chunk(0, chunks)

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.thread_num) as ex:
            futures = {ex.submit(self.upload_chunk, i, chunks): i for i in range(1, chunks)}
            try:
                for future in concurrent.futures.as_completed(futures):
                    if self.failed:
                        print('Failed!')
                        for future in futures:
                            future.cancel()
                        return
            except KeyboardInterrupt:
                print('\nUser cancelled the operation.')
                for future in futures:
                    future.cancel()
                return

        if self.pbar:
            for bar in self.pbar:
                bar.close()
        print('')
        if 'url' not in self.data:
            print('Something went wrong. Upload failed.', self.data)
        return self

    def get_download_page(self):
        if not self.data or not 'url' in self.data:
            return
        f = Path(self.uri)
        print(f"Finished at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}, filename: {f.name}, size: {self.bytes_to_size_str(f.stat().st_size)}")
        print(self.data['url'])
        return self.data['url']

    def download(self, filename):
        m = re.search(r'^https?:\/\/\d+?\.gigafile\.nu\/([a-z0-9-]+)$', self.uri)
        if not m:
            print('Invalid URL.')
            return
        r = self.session.get(self.uri) # setup cookie
        try:
            soup = BeautifulSoup(r.text, 'html.parser')
            if soup.select_one('#contents_matomete'):
                ele = soup.select_one('.matomete_file')
                web_name = ele.select_one('.matomete_file_info > span:nth-child(2)').text.strip()
                file_id = re.search(r'download\(\d+, *\'(.+?)\'', ele.select_one('.download_panel_btn_dl')['onclick'])[1]
                size_str = re.search(r'（(.+?)）', ele.select_one('.matomete_file_info > span:nth-child(3)').text.strip())[1]
            else:
                file_id = m[1]
                size_str = soup.select_one('.dl_size').text.strip()
                web_name = soup.select_one('#dl').text.strip()

            print(f'Name: {web_name}, size: {size_str}, id: {file_id}')
        except Exception as ex:
            print(f'ERROR! Failed to parse the page {self.uri}.')
            print(ex)

        os.mkdir(filename)
        filename = filename + '\\' + re.sub(r"[\\/:*?'<>|]", '_', web_name)

        download_url = self.uri.rsplit('/', 1)[0] + '/download.php?file=' + file_id
        if self.aria2:
            cookie_str = '; '.join([f'{cookie.name}={cookie.value}' for cookie in self.session.cookies])
            cmd = ['aria2c', download_url, '--header', f'Cookie: {cookie_str}', '-o', filename]
            cmd.extend(self.aria2.split(' '))
            run(cmd)
            return

        temp = filename + '.dl'

        with self.session.get(download_url, stream=True) as r:
            r.raise_for_status()
            filesize = int(r.headers['Content-Length'])
            if self.progress:
                desc = filename if len(filename) <= 20 else filename[0:11] + '..' + filename[-7:]
                self.pbar = tqdm(total=filesize, unit='B', unit_scale=True, unit_divisor=1024, desc=desc)
            with open(temp, 'wb') as f:
                for chunk in r.iter_content(chunk_size=self.chunk_copy_size):
                    f.write(chunk)
                    if self.pbar:
                        self.pbar.update(len(chunk))
        if self.pbar:
            self.pbar.close()

        filesize_downloaded = Path(temp).stat().st_size
        print(f'Filesize check: expected: {filesize}; actual: {filesize_downloaded}')
        if filesize == filesize_downloaded:
            print('Succeeded.')
            rename(temp, filename)
        else:
            print(f'Downloaded file is corrupt. Please check the broken file at {temp} and delete it yourself if needed.')
        return filename


@bot.event
async def on_ready():
    main = Main(bot)
    await bot.add_cog(main)
    await main.start_queue_processor()
    await main.bot.tree.sync(guild=discord.Object(id=GUILD_ID))

bot.run(TOKEN)