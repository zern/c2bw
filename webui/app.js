(function () {
  'use strict';

  new Vue({
    el: '#app',
    data: function () {
      return {
        bridgeReady: false,
        updateAvailable: false,
        updateInfo: {},
        directoryLoading: '',
        processing: false,
        paused: false,
        phase: 'idle',
        progress: 0,
        status: '准备就绪',
        pollTimer: null,
        bridgeRetryTimer: null,
        polling: false,
        form: {
          source_dir: '',
          target_dir: '',
          include_subfolders: false,
          enable_binarize: true,
          bin_method: '0',
          threshold_val: 50,
          non_bin_format: 'keep',
          enable_crop: true,
          crop_percent: 50,
          crop_direction: 'R2L',
          exclude_ratio: 0.7,
          max_threads: 8,
          enable_pdf: false
        }
      };
    },
    computed: {
      canPause: function () {
        return this.processing && this.phase === 'processing';
      },
      phaseText: function () {
        var names = {
          idle: '任务进度',
          processing: this.paused ? '处理已暂停' : '正在处理图片',
          awaiting_pdf: '等待 PDF 确认',
          pdf: '正在生成 PDF',
          cancelling: '正在取消任务',
          finishing: '正在完成任务'
        };
        return names[this.phase] || '任务进度';
      }
    },
    mounted: function () {
      var vm = this;
      function connectBridge() {
        if (vm.bridgeReady) {
          return;
        }
        // pywebview 注入 API 的时机在不同后端（尤其是 Win7/MSHTML）可能不同，
        // 不能只依赖一次 pywebviewready 事件。
        if (!window.pywebview || !window.pywebview.api) {
          vm.status = '正在连接本地处理服务…';
          return;
        }
        vm.bridgeReady = true;
        vm.status = '准备就绪';
        vm.checkForUpdates();
        if (vm.bridgeRetryTimer) {
          window.clearInterval(vm.bridgeRetryTimer);
          vm.bridgeRetryTimer = null;
        }
        vm.pollTimer = window.setInterval(function () {
          vm.pollEvents();
        }, 180);
        vm.pollEvents();
      }

      document.addEventListener('pywebviewready', connectBridge);
      window.addEventListener('pywebviewready', connectBridge);
      connectBridge();
      vm.bridgeRetryTimer = window.setInterval(connectBridge, 250);
    },
    beforeDestroy: function () {
      if (this.pollTimer) {
        window.clearInterval(this.pollTimer);
      }
      if (this.bridgeRetryTimer) {
        window.clearInterval(this.bridgeRetryTimer);
      }
    },
    methods: {
      callApi: function (method, args) {
        var vm = this;
        var attempts = 0;
        return new Promise(function (resolve, reject) {
          function invoke() {
            if (window.pywebview && window.pywebview.api &&
                typeof window.pywebview.api[method] === 'function') {
              try {
                window.pywebview.api[method].apply(null, args || [])
                  .then(resolve, reject);
              } catch (error) {
                reject(error);
              }
              return;
            }
            attempts += 1;
            if (attempts >= 25) {
              reject(new Error('本地处理服务尚未连接，请稍候重试。'));
              return;
            }
            window.setTimeout(invoke, 200);
          }
          invoke();
        });
      },
      showError: function (error) {
        var message = error && (error.error || error.message) ?
          (error.error || error.message) : String(error || '未知错误');
        this.$message.error({ message: message, duration: 5000, showClose: true });
      },
      checkForUpdates: function () {
        var vm = this;
        vm.callApi('get_update_info').then(function (result) {
          if (!result || !result.ok || !result.update || !result.update.version) return;
          vm.updateInfo = result.update;
          vm.updateAvailable = vm.compareVersions(result.current_version, result.update.version) < 0;
        }).catch(function () {});
      },
      compareVersions: function (current, latest) {
        var a = String(current || '0').split('.'), b = String(latest || '0').split('.');
        var length = Math.max(a.length, b.length), i;
        for (i = 0; i < length; i += 1) {
          var av = parseInt(a[i] || '0', 10) || 0, bv = parseInt(b[i] || '0', 10) || 0;
          if (av !== bv) return av < bv ? -1 : 1;
        }
        return 0;
      },
      downloadUpdate: function () {
        var vm = this;
        vm.callApi('open_download_url', [vm.updateInfo.download_url]).then(function (result) {
          if (!result.ok) vm.showError(result);
        }).catch(vm.showError);
      },
      selectDirectory: function (field) {
        var vm = this;
        vm.directoryLoading = field;
        vm.callApi('choose_directory', [vm.form[field] || '']).then(function (result) {
          vm.directoryLoading = '';
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          if (!result.path) {
            return;
          }
          vm.form[field] = result.path;
          if (field === 'source_dir') {
            vm.form.target_dir = result.path.replace(/[\\\/]+$/, '') + '\\\\output';
          }
        }).catch(function (error) {
          vm.directoryLoading = '';
          vm.showError(error);
        });
      },
      startTask: function () {
        var vm = this;
        vm.callApi('start_processing', [vm.form]).then(function (result) {
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          vm.processing = true;
          vm.paused = false;
          vm.phase = 'processing';
          vm.progress = 0;
          vm.status = '正在扫描文件...';
          vm.form.target_dir = result.target_dir;
        }).catch(function (error) {
          vm.showError(error);
        });
      },
      togglePause: function () {
        var vm = this;
        vm.callApi('toggle_pause').then(function (result) {
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          vm.paused = result.paused;
          vm.status = result.message;
        }).catch(function (error) {
          vm.showError(error);
        });
      },
      cancelTask: function () {
        var vm = this;
        vm.$confirm(
          '取消后会删除本次输出目录及其中的所有文件。确定继续吗？',
          '取消任务',
          {
            type: 'warning',
            confirmButtonText: '确定取消',
            cancelButtonText: '继续处理',
            closeOnClickModal: false
          }
        ).then(function () {
          return vm.callApi('cancel_processing');
        }).then(function (result) {
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          vm.paused = false;
          vm.phase = 'cancelling';
          vm.status = result.message;
        }).catch(function (action) {
          if (action !== 'cancel' && action !== 'close') {
            vm.showError(action);
          }
        });
      },
      answerPdfPrompt: function (generatePdf) {
        var vm = this;
        vm.callApi('respond_pdf_prompt', [generatePdf]).then(function (result) {
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          vm.phase = generatePdf ? 'pdf' : 'finishing';
          if (generatePdf) {
            vm.progress = 0;
          }
          vm.status = result.message;
        }).catch(function (error) {
          vm.showError(error);
        });
      },
      askForPdf: function (includeSubfolders) {
        var vm = this;
        var message = includeSubfolders ?
          '已启用子文件夹处理，将按每个子文件夹分别生成 PDF。是否继续？' :
          '图片处理已经完成，是否合并输出为单个 PDF？';
        vm.$confirm(message, '合并输出为 PDF', {
          type: 'info',
          confirmButtonText: '生成 PDF',
          cancelButtonText: '保持图片输出',
          distinguishCancelAndClose: true,
          closeOnClickModal: false
        }).then(function () {
          vm.answerPdfPrompt(true);
        }).catch(function () {
          vm.answerPdfPrompt(false);
        });
      },
      finishTask: function (event) {
        var vm = this;
        vm.processing = false;
        vm.paused = false;
        vm.phase = 'idle';
        vm.progress = 100;
        vm.status = event.message;
        vm.$alert(event.message, '任务结束', {
          confirmButtonText: '确定',
          type: event.can_open_output ? 'success' : 'info',
          dangerouslyUseHTMLString: false,
          customClass: 'result-dialog'
        }).then(function () {
          if (event.can_open_output) {
            return vm.callApi('open_output_folder');
          }
        }).then(function (result) {
          if (result && !result.ok) {
            vm.showError(result);
          }
        }).catch(function (error) {
          if (error !== 'cancel' && error !== 'close') {
            vm.showError(error);
          }
        });
      },
      handleEvent: function (event) {
        if (event.type === 'progress') {
          this.progress = Math.max(0, Math.min(100, event.percent));
          this.status = event.message;
        } else if (event.type === 'status') {
          this.status = event.message;
          if (event.message.indexOf('PDF') >= 0) {
            this.phase = 'pdf';
            this.progress = 0;
          }
        } else if (event.type === 'ask_pdf') {
          this.progress = 100;
          this.phase = 'awaiting_pdf';
          this.status = '图片处理完成，等待选择是否生成 PDF。';
          this.askForPdf(event.include_subfolders);
        } else if (event.type === 'finish') {
          this.finishTask(event);
        }
      },
      pollEvents: function () {
        var vm = this;
        if (!vm.bridgeReady || vm.polling) {
          return;
        }
        vm.polling = true;
        vm.callApi('poll_events').then(function (result) {
          var index;
          vm.polling = false;
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          vm.processing = result.processing;
          vm.paused = result.paused;
          vm.phase = result.phase;
          for (index = 0; index < result.events.length; index += 1) {
            vm.handleEvent(result.events[index]);
          }
        }).catch(function (error) {
          vm.polling = false;
          vm.showError(error);
        });
      }
    }
  });
}());
