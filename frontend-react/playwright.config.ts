import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
export default defineConfig({testDir:'./e2e',workers:1,timeout:90000,use:{baseURL:'http://127.0.0.1:15173',viewport:{width:1440,height:1000},trace:process.env.FLARE_BROWSER_TRACE==='1'?'on':'retain-on-failure',screenshot:'only-on-failure',launchOptions:{executablePath:process.env.FLARE_CHROME_PATH||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',args:['--use-angle=metal']}},webServer:{command:`"${root}/venv/bin/python" -m scripts.swarm_test_services`,cwd:root,url:'http://127.0.0.1:15173',reuseExistingServer:false,gracefulShutdown:{signal:'SIGTERM',timeout:15000},timeout:120000}});
