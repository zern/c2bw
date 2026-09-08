(function () {
  'use strict';

  var i18n = window.c2bwI18n || {
    getCurrentLanguage: function () { return 'zh-CN'; },
    supportedLanguages: [{ code: 'zh-CN', label: '简体中文' }],
    t: function (key) { return key; },
    setLanguage: function (l) { return l; }
  };

  new Vue({
    el: '#app',
    data: function () {
      var initialLang = i18n.getCurrentLanguage();
      return {
        bridgeReady: false,
        isDragging: false,
        updateAvailable: false,
        updateInfo: {},
        directoryLoading: '',
        processing: false,
        paused: false,
        phase: 'idle',
        progress: 0,
        currentLang: initialLang,
        supportedLanguages: i18n.supportedLanguages,
        status: i18n.t('statusReady', null, initialLang),
        pollTimer: null,
        bridgeRetryTimer: null,
        workMode: 'dir',
        pdfForm: {
          pdf_path: '',
          no_convert_pdf: false
        },
        form: {
          source_dir: '',
          target_dir: '',
          include_subfolders: false,
          keep_images_after_pdf: false,
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
      currentLanguageLabel: function () {
        var vm = this;
        for (var i = 0; i < vm.supportedLanguages.length; i++) {
          if (vm.supportedLanguages[i].code === vm.currentLang) {
            return vm.supportedLanguages[i].label;
          }
        }
        return vm.supportedLanguages.length > 0 ? vm.supportedLanguages[0].label : 'Language';
      },
      computedPdfTargetDir: function () {
        if (!this.pdfForm.pdf_path) {
          return '';
        }
        var path = this.pdfForm.pdf_path;
        var lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
        var dir = lastSlash >= 0 ? path.substring(0, lastSlash) : '';
        var filename = lastSlash >= 0 ? path.substring(lastSlash + 1) : path;
        var dotIdx = filename.lastIndexOf('.');
        var stem = dotIdx >= 0 ? filename.substring(0, dotIdx) : filename;
        var suffix = '';
        if (this.form.enable_crop && this.form.enable_binarize) {
          suffix = this.$t('pdfSuffixCroppedBin');
        } else if (this.form.enable_crop) {
          suffix = this.$t('pdfSuffixCropped');
        } else if (this.form.enable_binarize) {
          suffix = this.$t('pdfSuffixBin');
        }
        if (!suffix) {
          if (this.pdfForm.no_convert_pdf) {
            return (dir ? dir + '\\' : '') + stem;
          }
          return this.$t('pdfSuffixNone');
        }
        return (dir ? dir + '\\' : '') + stem + suffix;
      },
      phaseText: function () {
        var map = {
          idle: this.$t('phaseIdle'),
          extracting_pdf: this.$t('phaseExtractingPdf'),
          processing: this.paused ? this.$t('phasePaused') : this.$t('phaseProcessing'),
          awaiting_pdf: this.$t('phaseAwaitingPdf'),
          pdf: this.$t('phasePdf'),
          cancelling: this.$t('phaseCancelling'),
          finishing: this.$t('phaseFinishing')
        };
        return map[this.phase] || this.$t('phaseIdle');
      },
      singleBoxWidth: function () {
        var ratio = parseFloat(this.form.exclude_ratio);
        if (isNaN(ratio) || ratio <= 0) {
          ratio = 0.7;
        }
        return Math.min(84, Math.max(16, Math.round(48 * ratio)));
      },
      cropPercentNum: function () {
        var pct = parseInt(this.form.crop_percent, 10);
        if (isNaN(pct) || pct < 1 || pct > 100) {
          pct = 50;
        }
        return pct;
      },
      overlapPercent: function () {
        return Math.max(0, 2 * this.cropPercentNum - 100);
      },
      gapPercent: function () {
        return Math.max(0, 100 - 2 * this.cropPercentNum);
      },
      spreadCaptionText: function () {
        var p = this.cropPercentNum;
        var pageA = this.form.crop_direction === 'R2L' ? this.$t('captionR2L') : this.$t('captionL2R');
        if (p > 50) {
          return this.$t('captionOverlap', { crop: p, overlap: this.overlapPercent, order: pageA });
        } else if (p === 50) {
          return this.$t('captionEqual', { order: pageA });
        } else {
          return this.$t('captionGap', { crop: p, gap: this.gapPercent, order: pageA });
        }
      },
      spreadBoxTooltip: function () {
        var p = this.cropPercentNum;
        if (p > 50) {
          return this.$t('tooltipOverlap', { crop: p, overlap: this.overlapPercent });
        } else if (p === 50) {
          return this.$t('tooltipEqual');
        } else {
          return this.$t('tooltipGap', { crop: p, gap: this.gapPercent });
        }
      }
    },
    created: function () {
      var vm = this;
      window.__onNativeFileDrop = function (paths) {
        if (paths && paths.length > 0) {
          vm.applyDroppedPath(paths[0]);
        }
      };
    },
    mounted: function () {
      var vm = this;
      document.title = vm.$t('appTitle');

      function connectBridge() {
        if (vm.bridgeReady) {
          return;
        }
        if (!window.pywebview || !window.pywebview.api) {
          vm.status = vm.$t('statusConnecting');
          return;
        }
        vm.bridgeReady = true;
        vm.status = vm.$t('statusReady');

        // 从后端同步用户的语言习惯（保持关闭时的语言状态；未设置时自动适配系统语言）
        try {
          vm.callApi('get_user_language').then(function (res) {
            if (res && res.effective_language) {
              vm.changeLanguage(res.effective_language, false);
            }
          }).catch(function () {});
        } catch (e) {}

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

      // 注册拖拽与释放事件监听
      var dragCounter = 0;
      window.addEventListener('dragenter', function (e) {
        e.preventDefault();
        dragCounter += 1;
        vm.isDragging = true;
      });
      window.addEventListener('dragover', function (e) {
        e.preventDefault();
      });
      window.addEventListener('dragleave', function (e) {
        e.preventDefault();
        dragCounter -= 1;
        if (dragCounter <= 0) {
          dragCounter = 0;
          vm.isDragging = false;
        }
      });
      window.addEventListener('drop', function (e) {
        e.preventDefault();
        dragCounter = 0;
        vm.isDragging = false;
        var dt = e.dataTransfer;
        if (!dt) return;

        var droppedPath = '';
        var droppedName = '';

        if (dt.files && dt.files.length > 0) {
          var first = dt.files[0];
          droppedPath = first.path || '';
          droppedName = first.name || '';
        }

        if (!droppedName && dt.items && dt.items.length > 0) {
          for (var i = 0; i < dt.items.length; i++) {
            var item = dt.items[i];
            if (item.kind === 'file') {
              if (item.getAsFile) {
                var f = item.getAsFile();
                if (f) {
                  droppedPath = droppedPath || f.path || '';
                  droppedName = droppedName || f.name || '';
                }
              }
              if (!droppedName && item.webkitGetAsEntry) {
                var entry = item.webkitGetAsEntry();
                if (entry && entry.name) {
                  droppedName = entry.name;
                }
              }
              if (droppedPath || droppedName) break;
            }
          }
        }

        var target = droppedPath || droppedName;
        if (target) {
          vm.applyDroppedPath(target);
        }
      });

      window.addEventListener('beforeunload', function () {
        if (vm.currentLang) {
          try {
            vm.callApi('set_user_language', [vm.currentLang]);
          } catch (e) {}
        }
      });
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
      $t: function (key, params) {
        return window.c2bwI18n ? window.c2bwI18n.t(key, params, this.currentLang) : key;
      },
      changeLanguage: function (lang, persist) {
        if (window.c2bwI18n) {
          this.currentLang = window.c2bwI18n.setLanguage(lang);
          document.title = this.$t('appTitle');
          if (this.phase === 'idle' && !this.processing) {
            this.status = this.$t('statusReady');
          }
          if (persist !== false) {
            this.callApi('set_user_language', [lang]).catch(function () {});
          }
        }
      },
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
              reject(new Error(vm.$t('statusConnecting')));
              return;
            }
            window.setTimeout(invoke, 200);
          }
          invoke();
        });
      },
      showError: function (error) {
        var message = error && (error.error || error.message) ?
          (error.error || error.message) : String(error || this.$t('errUnknown'));
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
      applyDroppedPath: function (path) {
        var vm = this;
        vm.callApi('handle_dropped_path', [path]).then(function (result) {
          if (!result || !result.ok) {
            vm.showError(result ? result.error : vm.$t('errUnrecognizedDropPath'));
            return;
          }
          if (result.type === 'dir') {
            vm.workMode = 'dir';
            vm.form.source_dir = result.path;
            vm.form.target_dir = result.suggested_target_dir;
            vm.$message.success(vm.$t('loadedInputDir') + ': ' + result.path);
          } else if (result.type === 'pdf') {
            vm.workMode = 'pdf';
            vm.pdfForm.pdf_path = result.path;
            vm.$message.success(vm.$t('loadedPdfFile') + ': ' + result.path);
          } else if (result.type === 'image') {
            vm.workMode = 'dir';
            vm.form.source_dir = result.parent_dir;
            vm.form.target_dir = result.suggested_target_dir;
            vm.$message.success(vm.$t('loadedImageParentDir') + ': ' + result.parent_dir);
          }
        }).catch(function (error) {
          vm.showError(error);
        });
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
            vm.form.target_dir = result.path.replace(/[\\\/]+$/, '') + '\\output';
          }
        }).catch(function (error) {
          vm.directoryLoading = '';
          vm.showError(error);
        });
      },
      selectPdfFileForMode: function () {
        var vm = this;
        vm.directoryLoading = 'pdf_file';
        vm.callApi('choose_pdf_file', [vm.pdfForm.pdf_path || '']).then(function (result) {
          vm.directoryLoading = '';
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          if (result.cancelled) {
            return;
          }
          vm.pdfForm.pdf_path = result.pdf_path;
        }).catch(function (error) {
          vm.directoryLoading = '';
          vm.showError(error);
        });
      },
      selectPdfFile: function () {
        var vm = this;
        vm.directoryLoading = 'pdf_file';
        vm.callApi('choose_pdf_file', [vm.form.source_dir || '']).then(function (result) {
          vm.directoryLoading = '';
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          if (result.cancelled) {
            return;
          }

          var confirmMsg = vm.$t('pdfExtractConfirmMsg', { pdf: result.pdf_path, dir: result.extract_dir });
          if (result.dir_exists_nonempty) {
            confirmMsg += vm.$t('pdfExtractWarning');
          }

          vm.$confirm(confirmMsg, vm.$t('pdfExtractConfirmTitle'), {
            type: 'info',
            confirmButtonText: vm.$t('btnExtractConfirm'),
            cancelButtonText: vm.$t('btnCancelGeneral'),
            distinguishCancelAndClose: true,
            closeOnClickModal: false
          }).then(function () {
            vm.directoryLoading = 'pdf_file';
            return vm.callApi('start_pdf_extraction', [result.pdf_path, result.extract_dir]);
          }).then(function (startRes) {
            vm.directoryLoading = '';
            if (startRes && !startRes.ok) {
              vm.showError(startRes);
              return;
            }
            if (startRes) {
              vm.processing = true;
              vm.paused = false;
              vm.phase = 'extracting_pdf';
              vm.progress = 0;
              vm.status = vm.$t('phaseExtractingPdf');
            }
          }).catch(function (action) {
            vm.directoryLoading = '';
            if (action !== 'cancel' && action !== 'close') {
              vm.showError(action);
            }
          });
        }).catch(function (error) {
          vm.directoryLoading = '';
          vm.showError(error);
        });
      },
      startTask: function () {
        var vm = this;
        if (vm.workMode === 'pdf') {
          if (!vm.pdfForm.pdf_path) {
            vm.showError(vm.$t('errSelectPdf'));
            return;
          }
          if (!vm.form.enable_crop && !vm.form.enable_binarize) {
            if (!vm.pdfForm.no_convert_pdf) {
              vm.showError(vm.$t('errSelectTask'));
              return;
            }
          }
          var targetDir = vm.computedPdfTargetDir;
          var pdfSettings = Object.assign({}, vm.form, {
            pdf_path: vm.pdfForm.pdf_path,
            no_convert_pdf: vm.pdfForm.no_convert_pdf,
            lang: vm.currentLang
          });

          function runWorkflow() {
            vm.callApi('start_pdf_workflow', [pdfSettings]).then(function (result) {
              if (!result.ok) {
                vm.showError(result);
                return;
              }
              vm.processing = true;
              vm.paused = false;
              vm.phase = 'extracting_pdf';
              vm.progress = 0;
              vm.status = vm.$t('statusStartingWorkflow');
            }).catch(function (error) {
              vm.showError(error);
            });
          }

          var confirmMessage;
          if (vm.pdfForm.no_convert_pdf && !vm.form.enable_crop && !vm.form.enable_binarize) {
            confirmMessage = vm.$t('pdfWorkflowMsgExtract', { dir: targetDir });
          } else if (vm.pdfForm.no_convert_pdf) {
            confirmMessage = vm.$t('pdfWorkflowMsgProcess', { dir: targetDir });
          } else {
            confirmMessage = vm.$t('pdfWorkflowMsgReconstruct', { dir: targetDir });
          }

          vm.$confirm(
            confirmMessage,
            vm.$t('pdfWorkflowConfirmTitle'),
            {
              type: 'info',
              confirmButtonText: vm.$t('pdfWorkflowConfirmStart'),
              cancelButtonText: vm.$t('btnCancelGeneral'),
              closeOnClickModal: false
            }
          ).then(function () {
            runWorkflow();
          }).catch(function () {});
          return;
        }

        // 图片目录模式
        if (!vm.form.source_dir) {
          vm.showError(vm.$t('errSelectSourceDir'));
          return;
        }
        if (!vm.form.enable_crop && !vm.form.enable_binarize) {
          if (vm.form.non_bin_format === 'keep' && !vm.form.enable_pdf) {
            vm.showError(vm.$t('errSelectAnyTask'));
            return;
          }
        }

        vm.form.lang = vm.currentLang;
        vm.callApi('start_processing', [vm.form]).then(function (result) {
          if (!result.ok) {
            vm.showError(result);
            return;
          }
          vm.processing = true;
          vm.paused = false;
          vm.phase = 'processing';
          vm.progress = 0;
          vm.status = vm.$t('statusScanning');
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
          vm.$t('confirmCancelMsg'),
          vm.$t('confirmCancelTitle'),
          {
            type: 'warning',
            confirmButtonText: vm.$t('btnConfirmCancel'),
            cancelButtonText: vm.$t('btnContinueProcessing'),
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
          vm.$t('confirmPdfMsgSub') :
          vm.$t('confirmPdfMsgSingle');
        vm.$confirm(message, vm.$t('confirmPdfTitle'), {
          type: 'info',
          confirmButtonText: vm.$t('btnGeneratePdf'),
          cancelButtonText: vm.$t('btnKeepImages'),
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
        vm.$alert(event.message, vm.$t('taskFinishedTitle'), {
          confirmButtonText: vm.$t('btnConfirm'),
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
          this.status = this.$t('phaseAwaitingPdf');
          this.askForPdf(event.include_subfolders);
        } else if (event.type === 'pdf_extracted') {
          this.handlePdfExtracted(event);
        } else if (event.type === 'finish') {
          this.finishTask(event);
        }
      },
      handlePdfExtracted: function (event) {
        var vm = this;
        vm.processing = false;
        vm.paused = false;
        vm.phase = 'idle';

        if (event.error) {
          vm.status = vm.$t('errPdfExtract') + ': ' + event.error;
          vm.showError(event.error);
          return;
        }

        vm.progress = 100;
        vm.status = vm.$t('pdfExtractSuccess') + ': ' + event.count;
        vm.form.source_dir = event.extract_dir;
        vm.form.target_dir = event.extract_dir.replace(/[\\\/]+$/, '') + '\\output';

        var msg = vm.$t('pdfExtractedFollowupMsg', { count: event.count, dir: event.extract_dir });
        vm.$confirm(msg, vm.$t('pdfExtractedFollowupTitle'), {
          type: 'success',
          confirmButtonText: vm.$t('btnConfirm'),
          cancelButtonText: vm.$t('btnCancelGeneral'),
          distinguishCancelAndClose: true,
          closeOnClickModal: false
        }).then(function () {
          vm.startTask();
        }).catch(function () {});
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
