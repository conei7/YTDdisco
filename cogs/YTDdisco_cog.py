import re
import os
import sys
import time
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
import stat  # Fix missing import for delete_folder

GUILD_ID = 1084051938011795516
GUILD_ID = 1032196153325912125

authorized_list = [789784662246817792, 871373790750330930, 640139773192830977, 841634026337861653]

parent_dir = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))

lock = asyncio.Lock()

class Get_Command(commands.Cog):
    def __init__(self, bot:commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        await self.bot.tree.sync(guild=discord.Object(id=GUILD_ID))
        print('sync')

        await self.bot.change_presence(activity=discord.Game(name=''))

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
            # 最初にレスポンスを遅延
            await interaction.response.defer()
            
            try:
                # TXTファイルの内容を確認
                if not txtfile.filename.endswith('.txt'):
                    embed = discord.Embed(
                        description = 'アップロードされたファイルがTXTファイルではありません。',
                        color=discord.Color.red()
                    )
                    await interaction.followup.send(embed=embed, ephemeral=True)
                    return                
                # TXTファイルの内容を読み取り
                file_content = await txtfile.read()
                url_content = file_content.decode('utf-8')
                
                # OptionModalのインスタンスを作成し、直接実行
                self.modal = OptionModal(bot=self.bot, zipfile=zipfile, codec=codec, extension=extension, resolution=resolution, thumbnail=thumbnail, metadata=metadata, options=options, txt_content=url_content)                
                # modalのmainメソッドを直接呼び出し
                await self.modal.main(interaction)
                
            except Exception as e:
                embed = discord.Embed(
                    description = f'TXTファイルの処理中にエラーが発生しました: {str(e)}',
                    color=discord.Color.red()
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return
        else:
            # TXTファイルが添付されていない場合は通常のmodal表示
            self.modal = OptionModal(bot=self.bot, zipfile=zipfile, codec=codec, extension=extension, resolution=resolution, thumbnail=thumbnail, metadata=metadata, options=options, txt_content=None)
            await interaction.response.send_modal(self.modal)

    @app_commands.command(name = 'progress', description = '進捗を再送信')
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe()
    async def progress_send(self, interaction: discord.Interaction,) -> Callable[[discord.Interaction], Awaitable[None]]:
        await self.modal.progress_send(interaction)

    @app_commands.command(name = 'restart', description = 'ダウンロードを停止(botの再起動)')
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe()
    async def stop_download(self, interaction: discord.Interaction,) -> Callable[[discord.Interaction], Awaitable[None]]:
        embed = discord.Embed(
            description = f'Download was stopped by {interaction.user.display_name}',
            color=discord.Color.dark_red()
        )
        await interaction.response.send_message(embed=embed)

        python = sys.executable
        os.execl(python, python, *sys.argv)

    @app_commands.command(name = 'upgrade', description = 'upgrade yt-dlp')
    @app_commands.guilds(GUILD_ID)
    @app_commands.describe()
    async def upgrade(self, interaction: discord.Interaction,) -> Callable[[discord.Interaction], Awaitable[None]]:
        async with lock:
            author_id = interaction.user.id

            if author_id in authorized_list:
                await self.bot.change_presence(activity=discord.Game(name='Upgrade'))

                embed = discord.Embed(description = f'Upgrading yt-dlp',)
                await  interaction.response.send_message(embed=embed)

                try:
                    subprocess.run(['python', '-m', 'pip', 'install', '--upgrade', 'yt-dlp'])

                    embed = discord.Embed(description = 'Upgrade completed successfully')
                    await  interaction.channel.send(embed=embed,file=None)

                except subprocess.CalledProcessError as e:
                    embed = discord.Embed(description = f'Upgrade has failed\n{e}')
                    await  interaction.channel.send(embed=embed,file=None)

                await self.bot.change_presence(activity=discord.Game(name=''))

            else:
                embed = discord.Embed(description = f'Who the hell are you? Go home:middle_finger:')
                await  interaction.response.send_message(embed=embed)

class OptionModal(discord.ui.Modal):
    def __init__(self, bot: commands.Bot, zipfile: bool = True, extension: str = 'mp3', codec: str = 'default', resolution: str = 'best', thumbnail: bool = True, metadata: bool = True, options: str = '', txt_content: str = None) -> None:
        super().__init__(title='Input Download URL', timeout=None)
        self.zipfile = zipfile; self.extension = extension; self.codec = codec ;self.resolution = resolution; self.thumbnail = thumbnail; self.metadata = metadata; self.options = options.split(',')
        self.txt_content = txt_content  # TXTファイルからの内容

        #optionlist
        '''
        limit: Eliminate the limit on the number of downloads.
        nvidia: Encoding with gpu.
        dm: Run with dm.
        local: Save to local.
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
        self.run = True
        self.time = time.time()
        self.embed_color = discord.Color.dark_theme()
        self.progress_content = ''
        self.author_name = interaction.user.display_name
        self.author_url = interaction.user.avatar.url
        self.author = interaction.user

        #tasks = [self.edit_message(), self.main(interaction)]
        #await asyncio.gather(*tasks)
        await self.main(interaction)

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
        
        #同時に1つしか実行できないようにする
        async with lock:
            if ('dm' in self.options) and (interaction.user.id not in authorized_list):
                now = datetime.now(timezone(timedelta(hours=9)))
                unix_timestamp = int(datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=now.tzinfo).timestamp())
                today = int(datetime.now().strftime('%Y%m%d'))
                password = ((unix_timestamp+1)*today % 982451653)%10000
                if str(password) not in self.options:
                    self.options = []

            self.status_content = '[loading url]'
            # TXTファイルから直接呼び出された場合、既にdefer()されているかチェック
            if not interaction.response.is_done():
                await interaction.response.defer()

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
                # deferされている場合はfollowup.sendを使用
                if interaction.response.is_done():
                    self.msg = await interaction.followup.send(embed=embed, wait=True)
                else:
                    self.msg = await interaction.channel.send(embed=embed,file=None)
            self.edit_message.start()

            #self.msg = await interaction.followup.send(content='[initializing]', wait=True, ephemeral=self.ephemeral)

            if 'local' in self.options:
                temp_path = 'H:/'
            else:
                temp_path = os.path.join(parent_dir, 'YTD_temp')
                self.delete_folder(temp_path)

                uploads_dir = os.path.join(temp_path, 'uploads')
                os.makedirs(uploads_dir, exist_ok=True)


            self.input_url_list = self.url_input.value.split()
            url_list, self.num = self.get_urllist(self.input_url_list)

            if self.num > self.max_downloads:
                embed = discord.Embed(
                    description = f'The maximum number of files that can be downloaded at one time is {self.max_downloads}.',
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
                        try:
                            shutil.move(downloads_dir, uploads_dir)
                        except Exception as e:
                            print(f"Error moving directory {downloads_dir} to {uploads_dir}: {e}")
                            self.status_content = f"[error] Failed to move directory: {item[1]}"
                            self.embed_color = discord.Color.red()
                            await self.channel.send(content=f"Error moving directory: {item[1]}\n{e}")
                            continue

                    self.delete_folder(downloads_dir)
                    self.cnt += 1

                elif type(item) is str:
                    downloads_dir = os.path.join(temp_path, 'downloads')
                    try:
                        await asyncio.to_thread(self.download, downloads_dir, item, self.extension, self.resolution, self.thumbnail, self.metadata)
                    except Exception as e:
                        print(f"Download error for URL {item}: {e}")
                        self.status_content = f"[error] Download failed: {item}"
                        self.embed_color = discord.Color.red()
                        await self.channel.send(content=f"Download error for URL {item}: {e}")
                        continue

                    try:
                        download_path = os.path.join(downloads_dir, os.listdir(downloads_dir)[0])
                    except IndexError:
                        print(f"Error: Directory {downloads_dir} is empty.")
                        self.status_content = f"[error] Directory is empty: {downloads_dir}"
                        self.embed_color = discord.Color.red()
                        await self.channel.send(content=f"Error: Directory is empty: {downloads_dir}")
                        continue
                    except Exception as e:
                        print(f"Error accessing directory {downloads_dir}: {e}")
                        self.status_content = f"[error] Failed to access directory: {downloads_dir}"
                        self.embed_color = discord.Color.red()
                        await self.channel.send(content=f"Error accessing directory: {downloads_dir}\n{e}")
                        continue

                    if self.zipfile == False:
                        self.status_content = f'[uploading] {self.cnt}/{self.num} : {os.listdir(downloads_dir)[0]}'
                        self.embed_color = discord.Color.teal()
                        #self.progress_content = ''
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

    def delete_folder(self, folder: str) -> None:
        if os.path.isdir(folder):
            retries = 3
            for attempt in range(retries):
                try:
                    shutil.rmtree(folder)
                    break
                except PermissionError:
                    def remove_readonly(func, path, exc_info):
                        os.chmod(path, stat.S_IWRITE)
                        func(path)
                    time.sleep(1)  # Wait before retrying
                    try:
                        shutil.rmtree(folder, onerror=remove_readonly)
                        break
                    except Exception as retry_e:
                        if attempt == retries - 1:
                            print(f"Failed to delete folder after {retries} attempts: {folder} - {retry_e}")
                except Exception as e:
                    if attempt == retries - 1:
                        print(f"Error deleting folder {folder}: {e}")

    @tasks.loop(seconds=1)
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
                    'format': f'bv*[ext={extension}]+ba[ext=m4a]/b[ext={extension}] -S vcodec:h264' if resolution == 'best' else f'wv*[ext={extension}]+wa[ext=m4a]/w[ext={extension}]' if resolution == 'worst' else f'bv[ext={extension}][height<={resolution}]+ba[ext=m4a]/best',
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
            print('\n',e)

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
            return '0 B'  # Fix incomplete implementation
        units = ('B', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB')
        i = int(math.floor(math.log(bytes, 1024)))
        p = math.pow(1024, i)
        return f'{bytes / p:.02f} {units[i]}'

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


    def split_file(self, input_file, out, target_size=None, start=0, chunk_copy_size=1024 * 1024):
        # Placeholder for split_file implementation
        pass

    def upload_chunk(self, chunk_no, chunks):
        # Placeholder for upload_chunk implementation
        pass

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


async def setup(bot):
    await bot.add_cog(Get_Command(bot))