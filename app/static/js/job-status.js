(function () {
  'use strict';

  const POLL_INTERVAL_MS = 2000;
  const MAX_POLLS = 150;
  const monitor = document.querySelector('[data-job-monitor]');
  if (!monitor) return;

  const message = monitor.querySelector('[data-job-message]');
  if (!message) return;

  let statusUrl;
  try {
    statusUrl = new URL(monitor.dataset.statusUrl, window.location.href);
  } catch (_error) {
    return;
  }
  if (statusUrl.origin !== window.location.origin) return;

  let pollCount = 0;
  let timer = null;
  let stopped = false;

  function stop(text) {
    stopped = true;
    monitor.setAttribute('aria-busy', 'false');
    if (timer !== null) window.clearTimeout(timer);
    if (text) message.textContent = text;
  }

  function retry() {
    if (pollCount >= MAX_POLLS) {
      stop('任务仍在处理中');
      return;
    }
    timer = window.setTimeout(poll, POLL_INTERVAL_MS);
  }

  function showActive(job) {
    const label = job.status === 'running' ? '正在运行' : '已排队';
    message.textContent = `后台任务 #${job.job_id} ${label}`;
  }

  async function poll() {
    if (stopped) return;
    pollCount += 1;

    let response;
    try {
      response = await window.fetch(statusUrl.href, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' }
      });
    } catch (_error) {
      retry();
      return;
    }

    if (!response.ok) {
      stop('无法获取任务状态');
      return;
    }

    let job;
    try {
      job = await response.json();
    } catch (_error) {
      stop('无法获取任务状态');
      return;
    }

    if (job.status === 'queued' || job.status === 'running') {
      showActive(job);
      retry();
      return;
    }

    if (job.status === 'succeeded') {
      const taskId = job.result && job.result.task_id;
      const successUrl = monitor.dataset.successUrl || '';
      if (!Number.isInteger(taskId) || !successUrl.includes('{task_id}')) {
        stop('任务结果不可用');
        return;
      }

      let destination;
      try {
        destination = new URL(
          successUrl.replace('{task_id}', encodeURIComponent(String(taskId))),
          window.location.href
        );
      } catch (_error) {
        stop('任务结果不可用');
        return;
      }
      if (destination.origin !== window.location.origin) {
        stop('任务结果不可用');
        return;
      }
      stop();
      window.location.assign(destination.href);
      return;
    }

    if (job.status === 'dead') {
      const error = String(job.last_error || '任务执行失败').slice(0, 2000);
      stop(error);
      return;
    }

    stop('无法获取任务状态');
  }

  window.addEventListener('pagehide', function () { stop(); }, { once: true });
  poll();
}());
