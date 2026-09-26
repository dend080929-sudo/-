'use strict';

self.addEventListener('push',event=>{
  let data={title:'AUTOCAT JP',body:'運営から新しいお知らせがあります。',url:'/autocat/chat?room=admin',tag:'autocat-admin'};
  try{if(event.data)data={...data,...event.data.json()};}catch{}
  event.waitUntil(self.registration.showNotification('AUTOCAT JP',{
    body:String(data.body||'運営から新しいお知らせがあります。'),
    tag:String(data.tag||'autocat-admin'),
    data:{url:String(data.url||'/autocat/chat?room=admin')},
  }));
});

self.addEventListener('notificationclick',event=>{
  event.notification.close();
  const target=new URL(event.notification.data?.url||'/autocat/chat?room=admin',self.location.origin).href;
  event.waitUntil(clients.matchAll({type:'window',includeUncontrolled:true}).then(windows=>{
    for(const client of windows){if('focus'in client){client.navigate(target);return client.focus();}}
    return clients.openWindow?clients.openWindow(target):undefined;
  }));
});
