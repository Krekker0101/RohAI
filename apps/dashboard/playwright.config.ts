import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir:'./tests', fullyParallel:false, workers:1, timeout:45000,
  reporter:'list',
  use:{ baseURL:'http://127.0.0.1:8000', channel:'msedge', headless:true, viewport:{width:1440,height:1000}, screenshot:'only-on-failure', trace:'retain-on-failure' },
});
