'use strict';

// Preload bridge: the renderer only ever sees this narrow, validated surface.

const { contextBridge, ipcRenderer } = require('electron');

function subscribe(channel, callback) {
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.off(channel, listener);
}

contextBridge.exposeInMainWorld('sbx', {
  paths: () => ipcRenderer.invoke('sbx:paths'),
  status: () => ipcRenderer.invoke('sbx:status'),
  sessions: () => ipcRenderer.invoke('sbx:sessions'),
  events: (limit) => ipcRenderer.invoke('sbx:events', limit),
  run: (payload) => ipcRenderer.invoke('sbx:run', payload),
  cancel: () => ipcRenderer.invoke('sbx:cancel'),
  openAppHome: () => ipcRenderer.invoke('sbx:open-app-home'),

  onOutput: (callback) => subscribe('sbx:output', callback),
  onEvent: (callback) => subscribe('sbx:event', callback),
  onRunStarted: (callback) => subscribe('sbx:run-started', callback),
  onRunFinished: (callback) => subscribe('sbx:run-finished', callback),
  onStatusChanged: (callback) => subscribe('sbx:status-changed', callback),
  onCenterLog: (callback) => subscribe('sbx:center-log', callback),
});
