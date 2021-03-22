#!/usr/bin/python3
#
# Read data from Samsung SmartThings Hub.
#
# pip3 install paho-mqtt tendo
#
#
# To run in crontab, do the same again as root
# sudo su -
# pip3 install paho-mqtt tendo pyyaml
#
#
# CRONTAB:
# @reboot sleep 60;sudo --user=pi /home/pi/smartthings-mqtt-bridge/smartthings-mqtt-bridge.py
# 0 * * * * sudo --user=pi /home/pi/smartthings-mqtt-bridge/smartthings-mqtt-bridge.py
#

import re, requests, sys, os, logging, socket, argparse
import json, time, xml, urllib, requests, threading
import paho.mqtt.client as paho
import yaml, socketserver
from tendo import singleton
from logging.handlers import RotatingFileHandler
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

try:
    from ConfigParser import RawConfigParser
except ImportError as e:
    from configparser import RawConfigParser

config = {}
state = {}

# -------------------- LoadConfig -----------------------------------
def LoadConfig(conf_file):
    global config

    try:
        stream = open(conf_file, 'r')
        config = yaml.safe_load(stream)
    except yaml.YAMLError as exc:
        print(exc)

    if "mqtt" not in config:
        config["mqtt"] = {}
    if "preface" not in config["mqtt"]: 
        config["mqtt"]["preface"] = ''
    if "command_suffix" not in config["mqtt"]: 
        config["mqtt"]["command_suffix"] = ''
    if "state_read_suffix" not in config["mqtt"]: 
        config["mqtt"]["state_read_suffix"] = ''
    if "state_write_suffix" not in config["mqtt"]: 
        config["mqtt"]["state_write_suffix"] = ''
    if "retain" not in config["mqtt"]: 
        config["mqtt"]["retain"] = True
    if "host" not in config:
        # Get the actually used external IP, otherwise 127.0.0.1 or localhost may just be a loop back that is not contactable from Smartthings Hub
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        config["host"] = s.getsockname()[0]
        # config["host"] = "localhost"
        s.close()
    if "port" not in config:
        config["port"] = 8080
    if "port" not in config["mqtt"]:
        config["mqtt"]["port"] = 1883
    if "DebugLevel" not in config:
        config["DebugLevel"] = "info"
    if "EnableDiscovery" not in config:
        config["EnableDiscovery"] = False
        
    return True

# -------------------- Handle Messages -----------------------------------

def sendMessageToSmarthub(cmd):
    global state
    
    logger.info("Sending message to: http://"+state["callback"]+": "+json.dumps(cmd))
    r = requests.post("http://"+state["callback"] , json=cmd)    

def runWebServer():
    global config
    
    HTTPListener = HTTPServer((config["host"], config["port"]), receiveMessageFromSmarthub)
    logger.info("Listening at http://"+config["host"]+":"+str(config["port"]))
    HTTPListener.serve_forever()


class receiveMessageFromSmarthub(BaseHTTPRequestHandler):
    def log_request(self, code): 
        logger.info('Received http request: "%s" from %s. Return Code: %s', self.requestline, self.client_address[0], str(code))
               
    def do_POST(self):
        logger.info("Received message from SmartHub....")
        content_length = int(self.headers['Content-Length'])
        command = self.path.replace("/", "")
        post_data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        logger.debug("Received message from SmartHub for: "+command+" message: "+json.dumps(post_data, indent=4))
        
        if command == "push":
           topic = config['mqtt']['preface']+"/"+post_data["name"]+"/"+post_data["type"]+"/"+config['mqtt']['state_read_suffix']
           sendMQTT(topic, post_data["value"])
           state["history"][topic] = str(post_data["value"])
        elif command == "subscribe":
           oldSubscriptions = state["subscriptions"]
           state["subscriptions"] = []
           for property in post_data["devices"]:
                for device in post_data["devices"][property]:
                    state["subscriptions"].append(config['mqtt']['preface']+"/"+device+"/"+property+"/"+config['mqtt']['command_suffix'])
                    state["subscriptions"].append(config['mqtt']['preface']+"/"+device+"/"+property+"/"+config['mqtt']['state_write_suffix'])
           state["callback"] = post_data["callback"]
           writeStateFile()
           for topic in list(set(oldSubscriptions) - set(state["subscriptions"])):  # Remove subscription
               logger.info("removing subscription: "+topic)
               t.unsubscribe(topic)
               ## Home Assistant's mqtt discovery does not offer removal
           for topic in list(set(state["subscriptions"]) - set(oldSubscriptions)):    # Add subscriptions
               logger.info("adding subscription: "+topic)
               t.subscribe(topic)
               if config["EnableDiscovery"] == True:
                   sendStartupInfo(topic)
        else:
           logger.error("Received unknown command: "+command)
        
        self.send_response(200)
        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({'status': 'OK'}).encode('utf-8'))

def sendMQTT(topic, msg):
    logger.info("sending message to MQTT: " + topic + " = " + msg)
    t.publish(topic,msg,retain=((config["mqtt"]["retain"]) if (not "/button/state" in topic) else False))
    
def sendStartupInfo(subscriptions):
    done = {}
    for subscription in subscriptions:
        pieces = subscription.split("/")
        if pieces[1] not in done:
            topic = pieces[1].lower().replace(" ", "_").replace("(", "").replace(")", "") 
            if pieces[2] == "level": 
                sendMQTT("homeassistant/cover/"+topic+"/config", '{"name": "'+pieces[1]+'", "unique_id": "SmartThings_level_'+pieces[1]+'", "command_topic": "'+config['mqtt']['preface']+'/'+pieces[1]+'/level/cmd", "position_topic": "'+config['mqtt']['preface']+'/'+pieces[1]+'/level/'+config['mqtt']['state_read_suffix']+'", "set_position_topic": "'+config['mqtt']['preface']+'/'+pieces[1]+'/level/cmd", "payload_open": "100", "payload_close": "0", "state_open": "100", "state_closed": "0"}')
                done[pieces[1]] = True
            elif pieces[2] == "switch":
                sendMQTT("homeassistant/switch/"+topic+"/config", '{"name": "'+pieces[1]+'", "unique_id": "SmartThings_switch_'+pieces[1]+'", "command_topic": "'+config['mqtt']['preface']+'/'+pieces[1]+'/switch/cmd", "state_topic": "'+config['mqtt']['preface']+'/'+pieces[1]+'/switch/'+config['mqtt']['state_read_suffix']+'", "payload_on": "on", "payload_off": "off", "state_on": "on", "state_off": "off"}')
                done[pieces[1]] = True
            else: 
                logger.error("Unknown component: "+pieces[1]+" of type: "+pieces[2])
                    
def on_connect(client, userdata, flags, rc):
    global config

    logger.info("Connected to MQTT with result code "+str(rc))
    for topic in state["subscriptions"]:
        logger.info("Subscribe to topic: "+topic)
        t.subscribe(topic)
    if config["EnableDiscovery"] == True:
        logger.info("Sending Home Assistant MQTT Discovery messages")
        sendStartupInfo(state["subscriptions"])
 
def receiveMessageFromMQTT(client, userdata, message):
    logger.info("starting receiveMessageFromMQTT")
    try:
        msg = str(message.payload.decode("utf-8"))
        topic = message.topic
        logger.info("message received from MQTT: "+topic+" = "+msg)

        [prefix, name, property, command] = topic.split("/")
        if (command != config['mqtt']['state_read_suffix']):     
           state["history"][topic] = str(msg)

        topicSwitchState = prefix+"/"+name+"/switch/"+config['mqtt']['state_read_suffix']
        if (command == config['mqtt']['command_suffix']) and not ((property == "level") and (topicSwitchState in state["history"]) and (state["history"][topicSwitchState] == "off")):    #If sending level data and the switch is off, don't send anything SmartThings will turn the device on (which is confusing)
            topicLevelCommand = prefix+"/"+name+"/level/"+config['mqtt']['command_suffix']
            if (property == "switch") and (msg == "on") and (topicLevelCommand in state["history"]) and (int(state["history"][topicLevelCommand]) > 0):    # If sending switch data and there is already a nonzero level value, send level instead SmartThings will turn the device on
               property = "level"
               msg = state["history"][topicLevelCommand]
            cmd = {"name":name,"value":msg,"type":property,"command":(command == "" or (command != "" and command == config['mqtt']['command_suffix']))}
            logger.info("sending message: "+str(cmd))
            sendMessageToSmarthub(cmd)
        else :
            logger.error("received unkown message: "+topic+", message: "+msg)

    except Exception as e1:
        logger.critical("Exception Occured: " + str(e1))

    logger.info("finishing receiveMessageFromMQTT")
     

def loadStateFile():
    global state_file
    global state
    
    with open(state_file) as json_file:
         state = json.load(json_file)
         
def writeStateFile():
    global state_file
    global state

    with open(state_file, 'w') as outfile:
        json.dump(state, outfile, indent=4, sort_keys=True)
        outfile.write("\n") 

if __name__ == '__main__':
    ACTION_BASE_URL = 'http://purenetworks.com/HNAP1/'
    LEVELS = {'debug': logging.DEBUG,
              'info': logging.INFO,
              'warning': logging.WARNING,
              'error': logging.ERROR,
              'critical': logging.CRITICAL}
              
    curr_path = os.path.dirname(__file__)
    curr_name = os.path.basename(__file__)
    log_name = curr_name.replace(".py", ".log")
    log_file = curr_path+"/"+log_name
    log_formatter = logging.Formatter('%(asctime)s %(levelname)s %(funcName)s(%(lineno)d) %(message)s')
    
    state_file = curr_path+"/state.json"

    rotatingHandler = RotatingFileHandler(log_file, mode='a', maxBytes=1*1024*1024, backupCount=2, encoding=None, delay=0)
    rotatingHandler.setFormatter(log_formatter)
    rotatingHandler.setLevel(logging.INFO)

    logger = logging.getLogger('root')
    logger.addHandler(rotatingHandler)
    

    # Make sure we are not running already. Otherwise Exit
    try:
       tmp = logging.getLogger().level
       logging.getLogger().setLevel(logging.CRITICAL) # we do not want to see the warning
       me = singleton.SingleInstance() # will sys.exit(-1) if other instance is running
       logging.getLogger().setLevel(tmp)
    except:
       logging.getLogger().setLevel(logging.INFO)
       logger.info("Another instance is already running. quiting...")
       exit()

    # Now read the config file
    parser = argparse.ArgumentParser(description='SMARTTHINGS MQTT Bride for Home Assistant.')
    parser.add_argument('-config', '-c', dest='ConfigFile', default=curr_path+'/config.yml', help='Name of the Config File (incl full Path)')
    args = parser.parse_args()

    if args.ConfigFile == None:
        conf_file = curr_path+"/config.yml"
    else:
        conf_file = args.ConfigFile

    if not os.path.isfile(conf_file):
        logger.info("Creating new config file : " + conf_file)
        defaultConfigFile = curr_path+'/_config.yml'
        if not os.path.isfile(defaultConfigFile):
            logger.critical("Failure to create new config file: "+defaultConfigFile)
            sys.exit(1)
        else:
            copyfile(defaultConfigFile, conf_file)

    if not LoadConfig(conf_file):
        logger.critical("Failure to load configuration parameters")
        sys.exit(1) 
      
    level = LEVELS[config["DebugLevel"]]
    logging.getLogger().setLevel(level)
    rotatingHandler.setLevel(level)
    
    loadStateFile()
    
    t = paho.Client(client_id="smartthings-mqtt-bridge")                           #create client object
    t.username_pw_set(username=config["mqtt"]["username"],password=config["mqtt"]["password"])
    t.connect(config["mqtt"]["host"],config["mqtt"]["port"])
    logger.info("Connected to MQTT on "+config["mqtt"]["host"]+":"+str(config["mqtt"]["port"]))

    background_thread = threading.Thread(target=runWebServer)
    background_thread.daemon = True
    background_thread.start()
    logger.info("Started WebServer Thread to listen to messages from SmartHub")
    
    t.on_connect = on_connect
    t.on_message=receiveMessageFromMQTT
    logger.info("Starting Listener Thread to listen to messages from MQTT")
    t.loop_start()

    if config["EnableDiscovery"] == True:
        sendStartupInfo(state["subscriptions"])

    while True:
       time.sleep(15*60)
       writeStateFile()

    t.loop_stop()
    logger.info("Stop Listener Thread to listen to messages from MQTT")
        
    del me
    sys.exit()
    
