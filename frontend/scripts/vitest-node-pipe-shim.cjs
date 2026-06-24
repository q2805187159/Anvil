const childProcess = require("node:child_process");
const { EventEmitter } = require("node:events");
const { PassThrough } = require("node:stream");

const originalExec = childProcess.exec;

childProcess.exec = function exec(command, options, callback) {
  let cb = callback;
  let opts = options;
  if (typeof opts === "function") {
    cb = opts;
    opts = undefined;
  }

  if (typeof command === "string" && command.trim().toLowerCase() === "net use") {
    const child = new EventEmitter();
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
    child.kill = () => false;
    child.pid = 0;

    process.nextTick(() => {
      child.stdout.end("");
      child.stderr.end("");
      if (cb) {
        cb(null, "", "");
      }
      child.emit("exit", 0, null);
      child.emit("close", 0, null);
    });

    return child;
  }

  return originalExec.call(this, command, opts, cb);
};
