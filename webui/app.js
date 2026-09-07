(function () {
  'use strict';

  new Vue({
    el: '#app',
    data: function () {
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
        status: '准备就绪',
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
          suffix = '_已裁切_黑白版';
        } else if (this.form.enable_crop) {
          suffix = '_已裁切';
        } else if (this.form.enable_binarize) {
          suffix = '_黑白版';
        }
        if (!suffix) {
          if (this.pdfForm.no_convert_pdf) {
            return (dir ? dir + '\\' : '') + stem;
          }
          return '（请至少勾选一种任务：裁切或黑白二值化）';
        }
        return (dir ? dir + '\\' : '') + stem + suffix;
      },
      phaseText: function () {
        var names = {
          idle: '任务进度',
          extracting_pdf: '正在提取 PDF 图片',
          processing: this.paused ? '处理已暂停' : '正在处理图片',
          awaiting_pdf: '等待 PDF 确认',
          pdf: '正在生成 PDF',
          cancelling: '正在取消任务',
          finishing: '正在完成任务'
        };
        return names[this.phase] || '任务进度';
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
        var pageA = this.form.crop_direction === 'R2L' ? '右页为第1页(_A)' : '左页为第1页(_A)';
        if (p > 50) {
          return '左右各裁切 ' + p + '%，中缝重叠 ' + this.overlapPercent + '%（保证中缝内容可阅读）· ' + pageA;
        } else if (p === 50) {
          return '左右各裁切 50%，居中均分裁切无重叠 · ' + pageA;
        } else {
          return '左右各裁切 ' + p + '%，中间未裁入 ' + this.gapPercent + '% · ' + pageA;
        }
      },
      spreadBoxTooltip: function () {
        var p = this.cropPercentNum;
        if (p > 50) {
          return '左右各占原图 ' + p + '% 宽度，中间 ' + this.overlapPercent + '% 为重复重叠区，确保中缝装订内容完整';
        } else if (p === 50) {
          return '左右各占 50% 宽度，居中均分裁切';
        } else {
          return '左右各占 ' + p + '% 宽度，中间 ' + this.gapPercent + '% 未裁入';
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
      applyDroppedPath: function (path) {
        var vm = this;
        vm.callApi('handle_dropped_path', [path]).then(function (result) {
          if (!result || !result.ok) {
            vm.showError(result ? result.error : '无法识别拖拽的文件或目录路径。');
            return;
          }
          if (result.type === 'dir') {
            vm.workMode = 'dir';
            vm.form.source_dir = result.path;
            vm.form.target_dir = result.suggested_target_dir;
            vm.$message.success('已载入输入图片目录：' + result.path);
          } else if (result.type === 'pdf') {
            vm.workMode = 'pdf';
            vm.pdfForm.pdf_path = result.path;
            vm.$message.success('已载入待处理 PDF 文件：' + result.path);
          } else if (result.type === 'image') {
            vm.workMode = 'dir';
            vm.form.source_dir = result.parent_dir;
            vm.form.target_dir = result.suggested_target_dir;
            vm.$message.success('已载入图片所在目录：' + result.parent_dir);
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
            vm.form.target_dir = result.path.replace(/[\\\/]+$/, '') + '\\\\output';
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

          var confirmMsg = '已选择 PDF 文件：\n' + result.pdf_path + '\n\n是否提取分页图片到同名目录：\n' + result.extract_dir + '？';
          if (result.dir_exists_nonempty) {
            confirmMsg += '\n\n注意：目标目录已存在且非空，继续提取可能会覆盖同名文件！';
          }

          vm.$confirm(confirmMsg, '提取确认', {
            type: 'info',
            confirmButtonText: '确认提取',
            cancelButtonText: '取消',
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
              vm.status = '正在读取并提取 PDF 原始分页图片...';
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
            vm.showError('请先选择待处理的 PDF 文件！');
            return;
          }
          if (!vm.form.enable_crop && !vm.form.enable_binarize) {
            if (!vm.pdfForm.no_convert_pdf) {
              vm.showError('请至少选择一种处理任务（裁切或黑白二值化）！');
              return;
            }
          }
          var targetDir = vm.computedPdfTargetDir;
          var pdfSettings = Object.assign({}, vm.form, {
            pdf_path: vm.pdfForm.pdf_path,
            no_convert_pdf: vm.pdfForm.no_convert_pdf
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
              vm.status = '正在启动 PDF 流水线任务...';
            }).catch(function (error) {
              vm.showError(error);
            });
          }

          var confirmMessage;
          if (vm.pdfForm.no_convert_pdf && !vm.form.enable_crop && !vm.form.enable_binarize) {
            confirmMessage = '将直接从 PDF 提取原始图片至同名目录（不进行裁切、色彩处理或转 PDF）：\n\n提取目录：\n' + targetDir + '\n\n是否确认开始？';
          } else if (vm.pdfForm.no_convert_pdf) {
            confirmMessage = '将对 PDF 依次执行：从 PDF 提取原始图片至生成目录 -> 批量预处理 -> 保留处理后的图片文件夹（不生成 PDF）。\n\n输出目录：\n' + targetDir + '\n\n是否确认开始？';
          } else {
            confirmMessage = '将对 PDF 依次执行：从 PDF 提取原始图片至生成目录 -> 批量预处理 -> 转换生成新 PDF -> 自动删除分页图片。\n\n生成目录：\n' + targetDir + '\n\n是否确认开始？';
          }

          vm.$confirm(
            confirmMessage,
            '开始 PDF 任务',
            {
              type: 'info',
              confirmButtonText: '立即开始',
              cancelButtonText: '取消',
              closeOnClickModal: false
            }
          ).then(function () {
            runWorkflow();
          }).catch(function () {});
          return;
        }

        // 图片目录模式
        if (!vm.form.source_dir) {
          vm.showError('请先选择输入图片目录！');
          return;
        }
        if (!vm.form.enable_crop && !vm.form.enable_binarize) {
          if (vm.form.non_bin_format === 'keep' && !vm.form.enable_pdf) {
            vm.showError('请至少启用一种处理任务（色彩处理或分页裁切），或选择转为 JPG，或勾选合并输出为 PDF！');
            return;
          }
        }

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
          vm.status = 'PDF 提取失败：' + event.error;
          vm.showError(event.error);
          return;
        }

        vm.progress = 100;
        vm.status = 'PDF 图片提取完成，共提取 ' + event.count + ' 张图片。';
        vm.form.source_dir = event.extract_dir;
        vm.form.target_dir = event.extract_dir.replace(/[\\\/]+$/, '') + '\\output';

        var msg = '已成功从 PDF 提取 ' + event.count + ' 张图片到文件夹：\n' + event.extract_dir + '\n\n是否立即对此文件夹执行后续裁切、黑白二值化和 PDF 汇总处理？';
        vm.$confirm(msg, '执行后续预处理', {
          type: 'success',
          confirmButtonText: '立即执行',
          cancelButtonText: '仅保留路径',
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
