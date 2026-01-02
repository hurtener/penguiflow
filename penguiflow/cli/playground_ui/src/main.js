import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'
import { installGlobalErrorHandlers } from './lib/services/error-logger'

installGlobalErrorHandlers();

const target = document.getElementById('app')
if (!target) {
  throw new Error('Missing #app element')
}

const app = mount(App, {
  target,
})

export default app
