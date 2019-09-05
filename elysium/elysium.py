import datetime
import tornado.escape
import tornado.ioloop
import tornado.web
import tornado.template as template
import os
import io
import string
import random
import requests
import MySQLdb
import hashlib
import base64
import hmac
import uuid
import boto
import json
import urllib
import time
import threading
import sys
import math

root = os.path.dirname(__file__)

entrypoint_elysium = os.environ['ENTRY_POINT_ELYSIUM']
entrypoint_charon = os.environ['ENTRY_POINT_CHARON']

host_elysium = os.environ['HOST_ELYSIUM']
host_charon = os.environ['HOST_CHARON']

host_s3 = os.environ['HOST_S3']
bucketname = os.environ['BUCKET']

dbhost = os.environ['DBHOST']
dbuser = os.environ['DBUSER']
dbpasswd = os.environ['DBPASSWD']
dbname = os.environ['DBNAME']

api_secret = os.environ['API_SECRET']

scalefactor = int(os.environ['INSTANCESCALE'])
mininstances = int(os.environ['MININSTANCES'])
maxinstances = int(os.environ['MAXINSTANCES'])


def getConnection():
    db = MySQLdb.connect(host=dbhost,  # your host, usually localhost
                     user=dbuser,      # your username
                     passwd=dbpasswd,  # your password
                     db=dbname)        # name of the data base
    return(db)

def ec2thread(mininstances, maxinstances, scalefactor):
    while True:
        try:
            db = getConnection()
            cur = db.cursor()
            cur2 = db.cursor()
            
            # get current minsize (assuming equal to active instances)
            asg = os.popen("/root/.local/bin/aws autoscaling describe-auto-scaling-groups --auto-scaling-group-name EC2ContainerService-cloudalignment-EcsInstanceAsg-HL25MFFL1T8Q").read()
            jasg = json.loads(asg)
            currentInstances = jasg['AutoScalingGroups'][0]['MinSize']
            
            query = "SELECT id, submissiondate FROM jobqueue WHERE status='submitted'"
            cur.execute(query)
            for res in cur:
                print((datetime.datetime.utcnow() - res[1]).total_seconds()/60)
                if (datetime.datetime.utcnow() - res[1]).total_seconds()/60 > 60:
                    query = "UPDATE jobqueue SET status='failed' WHERE id=%s"
                    cur2.execute(query, (str(res[0]),))
            
            db.commit()
            
            cur = db.cursor()
            query = "SELECT COUNT(*) FROM jobqueue WHERE status='submitted' OR status='waiting'"
            cur.execute(query)
            count = cur.fetchone()[0]
            
            # there is no jobs in the queue that are currently processing or waiting, scale number of nodes to mininstances
            if count == 0 and int(currentInstances) > int(mininstances):
                os.system("/root/.local/bin/aws autoscaling update-auto-scaling-group --auto-scaling-group-name EC2ContainerService-cloudalignment-EcsInstanceAsg-HL25MFFL1T8Q --min-size "+str(mininstances)+" --max-size "+str(mininstances)+" --desired-capacity "+str(mininstances))
                print("scale down: "+str(currentInstances)+" -> "+str(mininstances))
            
            elif count > 0:
                cur = db.cursor()
                query = "SELECT COUNT(*) FROM jobqueue WHERE status='waiting'"
                cur.execute(query)
                count = cur.fetchone()[0]
                
                instanceCount = str(max(mininstances, min(maxinstances, int(math.ceil(count/float(scalefactor))))))
                
                if int(instanceCount) > int(currentInstances):
                    print("scale up: "+str(currentInstances)+" -> "+str(instanceCount))
                    os.system("/root/.local/bin/aws autoscaling update-auto-scaling-group --auto-scaling-group-name EC2ContainerService-cloudalignment-EcsInstanceAsg-HL25MFFL1T8Q --min-size "+instanceCount+" --max-size "+instanceCount+" --desired-capacity "+instanceCount)
        except:
            print("an error occurred, shake it off")
        
        try:
            db.close()
        except:
            print("failed to close connection")
        
        time.sleep(30)

class AlignmentProgressHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        username = self.get_argument('username', True)
        password = self.get_argument('password', True)
        prefix = self.get_argument('prefix', True)
        pstatus = self.get_argument('status', True)
        
        url = host_charon+"/"+entrypoint_charon+"/login?username="+username+"&password="+password
        response = urllib.urlopen(url)
        data = json.loads(response.read())

        if data["status"] == "success":
            uuid = data["message"]
            db = getConnection()
            cur = db.cursor()
            
            if pstatus == True:
                if prefix != True:
                    query = "SELECT * FROM jobqueue WHERE userid=%s AND outname LIKE CONCAT(%s, '%%') ORDER BY id ASC"
                    cur.execute(query, (uuid, prefix, ))
                else:
                    query = "SELECT * FROM jobqueue WHERE userid=%s ORDER BY id ASC"
                    cur.execute(query, (uuid, ))
            elif prefix != True:
                query = "SELECT * FROM jobqueue WHERE userid=%s AND status=%s AND outname LIKE CONCAT(%s, '%%') ORDER BY id ASC"
                cur.execute(query, (uuid, pstatus, prefix, ))
            else:
                query = "SELECT * FROM jobqueue WHERE userid=%s AND status=%s ORDER BY id ASC"
                cur.execute(query, (uuid, pstatus, ))
            
            jobs = {}
            for res in cur:
                status = res[6]
                salt = res[5]
                fname = res[3]
                
                job = { 'id': res[0],
                         'uid': res[1],
                         'user': res[2],
                         'datalink': res[3],
                         'outname': res[4],
                         'organism': res[5],
                         'status': res[6],
                         'creationdate': str(res[7]),
                         'submissiondate': str(res[8]),
                         'finishdate': str(res[9])}
                jobs[res[0]] = job
            self.write(jobs)
        else:
            response = { 'action': 'list jobs',
                 'task': username,
                 'status': 'error',
                 'message': 'login failed'}
            self.write(response)

class CreateJobHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        username = self.get_argument('username', True)
        password = self.get_argument('password', True)
        file1 = self.get_argument('file1', True)
        file2 = self.get_argument('file2', True)
        organism = self.get_argument('organism', True)
        outname = self.get_argument('outname', True)
        
        url = host_charon+"/"+entrypoint_charon+"/login?username="+username+"&password="+password
        response = urllib.urlopen(url)
        data = json.loads(response.read())
        uuid = data["message"]
        
        if data["status"] == "success":
            url = host_charon+"/"+entrypoint_charon+"/files?username="+username+"&password="+password
            response = urllib.urlopen(url)
            data = json.loads(response.read())
            
            files = data["filenames"]
            print(file1)
            print(file2)
            
            if file2 == True:
                if (file1 in files):
                    datalink = host_s3+"/"+bucketname+"/"+uuid+"/"+file1
                    
                    h2 = hashlib.md5()
                    h2.update((outname+datalink).encode('utf-8'))
                    uid = h2.hexdigest()
                    
                    db = getConnection()
                    cur = db.cursor()
                    query = "INSERT INTO jobqueue (uid, userid, datalink, outname, organism) VALUES (%s, %s, %s, %s, %s)"
                    
                    cur.execute(query, (uid, uuid, datalink, outname, organism, ))
                    db.commit()
                    cur.close()
                    db.close()
                    response = { 'action': 'create job',
                         'task': username,
                         'status': 'success',
                         'message': uid}
                    self.write(response)
                else:
                    response = { 'action': 'create job',
                         'task': username,
                         'status': 'error',
                         'message': 'file not found'}
                    self.write(response)
            else:
                if (file1 in files) & (file2 in files):
                    datalink = host_s3+"/"+bucketname+"/"+uuid+"/"+file1+";"+host_s3+"/"+bucketname+"/"+uuid+"/"+file2
                    
                    h2 = hashlib.md5()
                    h2.update((outname+datalink).encode('utf-8'))
                    uid = h2.hexdigest()
                    
                    db = getConnection()
                    cur = db.cursor()
                    query = "INSERT INTO jobqueue (uid, userid, datalink, outname, organism) VALUES (%s, %s, %s, %s, %s)"
                    
                    cur.execute(query, (uid, uuid, datalink, outname, organism, ))
                    db.commit()
                    cur.close()
                    db.close()
                    response = { 'action': 'create job',
                         'task': username,
                         'status': 'success',
                         'message': uid}
                    self.write(response)
                else:
                    response = { 'action': 'create job',
                         'task': username,
                         'status': 'error',
                         'message': 'files not found'}
                    self.write(response)

class QueueViewHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        username = self.get_argument('username', True)
        password = self.get_argument('password', True)
        
        url = host_charon+"/"+entrypoint_charon+"/login?username="+str(username)+"&password="+str(password)
        response = urllib.urlopen(url)
        data = json.loads(response.read())
        uuid = data["message"]
        
        if data["status"] == "success":
            db = getConnection()
            cur = db.cursor()
            
            query = "SELECT * FROM jobqueue WHERE status='waiting' OR status='submitted'"
            
            cur.execute(query)
            
            response = {}
            
            subm = []
            usr = []
            datalinks = []
            outnames = []
            species = []
            
            for res in cur:
                if res[2] == data["message"]:
                    usr.append(1)
                    datalinks.append(res[3])
                    outnames.append(res[4])
                    species.append(res[5])
                else:
                    usr.append(0)
                    datalinks.append("")
                    outnames.append("")
                    species.append("")
                
                if res[6] == "submitted":
                    subm.append(2)
                else:
                    subm.append(1)
            
            response = { 'user': usr,
                         'submissionstatus': subm,
                         'files': datalinks,
                         'outname': outnames,
                         'organism': species,
                         'status': 'success'}
            self.write(response)
        else:
            response = { 'status': 'failed',
                         'message': 'credential error'}
            self.write(response)

class VersionHandler(tornado.web.RequestHandler):
    def get(self):
        response = { 'version': '1',
                     'last_build':  datetime.date.today().isoformat() }
        self.write(response)

class GiveJobHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        jpass = self.get_argument('pass', True)
        response = {}
        response["id"] = "empty"
        
        if jpass == api_secret:
            db = getConnection()
            cur = db.cursor()
            query = "SELECT * FROM jobqueue WHERE status='waiting' LIMIT 1"
            cur.execute(query)
            
            for res in cur:
                response["id"] = res[0]
                response["uid"] = res[1]
                response["userid"] = res[2]
                response["type"] = "sequencing"
                response["resultbucket"] = bucketname
                response["datalinks"] = res[3]
                response["outname"] = res[4]
                response["organism"] = res[5]
                query = "UPDATE jobqueue SET status='submitted', submissiondate=now() WHERE id=%s"
                cur.execute(query, (res[0], ))
                db.commit()
        
        self.write(response)

class FinishJobHandler(tornado.web.RequestHandler):
    def get(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        jpass = self.get_argument('pass', True)
        uid = self.get_argument('uid', True)
        
        response = {}
        if jpass == api_secret:
            db = getConnection()
            cur = db.cursor()
            query = "UPDATE jobqueue SET status='completed', finishdate=now() WHERE uid=%s"
            cur.execute(query, (uid,))
            db.commit()
            
            response["id"] = uid
            response["status"] = "completed"
        else:
            response["id"] = uid
            response["status"] = "failed"
        
        self.write(response)

class IndexHandler(tornado.web.RequestHandler):
    def get(self):
        loader = template.Loader("templates")
        self.write(loader.load("elysium_template.html").generate(charon_base=entrypoint_charon, elysium_base=entrypoint_elysium, s3_store = host_s3+"/"+bucketname))

application = tornado.web.Application([
    (r"/"+entrypoint_elysium+"/version", VersionHandler),
    (r"/"+entrypoint_elysium+"/givejob", GiveJobHandler),
    (r"/"+entrypoint_elysium+"/finishjob", FinishJobHandler),
    (r"/"+entrypoint_elysium+"/createjob", CreateJobHandler),
    (r"/"+entrypoint_elysium+"/progress", AlignmentProgressHandler),
    (r"/"+entrypoint_elysium+"/queueview", QueueViewHandler),
    (r"/"+entrypoint_elysium+"/index.html", IndexHandler),
    (r"/"+entrypoint_elysium+"/(.*)", tornado.web.StaticFileHandler, dict(path=root))
])

ec2t = threading.Thread(target=ec2thread, args=(mininstances, maxinstances, scalefactor, ))
ec2t.start()

if __name__ == "__main__":
    application.listen(5000)
    tornado.ioloop.IOLoop.instance().start()





