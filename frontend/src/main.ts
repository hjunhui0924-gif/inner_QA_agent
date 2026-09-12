import { createApp } from 'vue'
import { ElIcon } from 'element-plus'
import 'element-plus/es/components/icon/style/css'

import App from './App.vue'
import router from './router'
import './styles/tokens.css'
import './styles/base.css'

createApp(App).use(router).component('ElIcon', ElIcon).mount('#app')
