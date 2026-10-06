import json, http.server, socketserver, time
A1='Safex5zGFMJSFcUEBk4ZqvZD8HRRVycZ5RWgDJPUzufecPxcsUGPZEJFbmnZ9MdqcBd6YYj39TdeAWmwWDg6pEqQD4HGeSeo34e2N'
A2='Safex5zUNcH9k2mLL7s63wEcWYNr4L4F5diLWhGb37idS1qC8cNGSxLcC3731dzjNKXoDiQYt8jzyfwFDLHfmM6uJKdeuXQb4YE4M'
A3='Safex5zJy3HKcQWmxgFyvTYRrCT9UQ6hFo2wk1PB8ZLw2vBo7Gd9XzTqJrUJLLsmCwXyd8Fz3DmqMfDRtVVNqtXjb7qFw4mkxaB'
now=int(time.time())
STATE={'now':now,'ttl_hours':12.0,
 'summary':{'workers':3,'connected':2,'hashrate':6930.0,'accepted':151,'rejected':1,'addresses':3,'instances':3,'blocks':4},
 'workers':[
  {'address':A1,'worker':'macbook','ip':'100.64.1.7','accepted':120,'rejected':0,'hashrate':5600.0,'last_share':now-6,'connected':True,'diff':50000},
  {'address':A2,'worker':'mrr-rig-with-a-very-long-worker-name-123','ip':'203.0.113.9','accepted':30,'rejected':1,'hashrate':1330.0,'last_share':now-400,'connected':True,'diff':200000},
  {'address':A3,'worker':'default','ip':'198.51.100.4','accepted':1,'rejected':0,'hashrate':0.0,'last_share':now-3600*5,'connected':False,'diff':50000}],
 'addresses':[
  {'address':A2,'blocks':3,'hashrate':1330.0,'connected':1,'last_block':now-7200},
  {'address':A1,'blocks':1,'hashrate':5600.0,'connected':1,'last_block':now-90000},
  {'address':A3,'blocks':0,'hashrate':0.0,'connected':0,'last_block':0}],
 'blocks':[{'ts':now-7200,'height':2099601,'address':A2},{'ts':now-9000,'height':2099580,'address':A2},{'ts':now-90000,'height':2099000,'address':A1}]}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self,*a): pass
    def send(self,obj,code=200):
        b=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b)
    def do_GET(self):
        p=self.path
        if p=='/' or p=='/index.html':
            b=open('/data/projects/safex-node/dashboard/index.html','rb').read(); self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if p=='/api/get_info': return self.send({'status':'OK','mainnet':True,'offline':False,'height':2099579,'target_height':0,'tx_pool_size':2,'start_time':now-3*86400-5*3600,'free_space':2400000000000,'incoming_connections_count':3,'outgoing_connections_count':8,'difficulty':35022775,'target':120})
        if p=='/stratum-api/state': return self.send(STATE)
        if p=='/stratum-api/chain':
            blocks=[{'height':2101413-i,'hash':'%064x'%(2101413-i),'timestamp':now-120*i-30,'size':(96 if i%3 else 3937),'txs':(0 if i%3 else 2),'reward':400.0,'orphan':False,'found_by':(A2 if i==2 else (A1 if i==7 else None))} for i in range(12)]
            return self.send({'height':2101414,'mempool':2,'blocks':blocks,'now':now})
        self.send({},404)
socketserver.TCPServer.allow_reuse_address=True
socketserver.ThreadingTCPServer(('127.0.0.1',8111),H).serve_forever()
