/* afterPack: incrusta el icono de JARVIS en el .exe (signAndEditExecutable
   está en false porque winCodeSign no se puede extraer en este entorno). */
const { execFileSync } = require('child_process');
const path = require('path');

exports.default = async function (context) {
  if (context.electronPlatformName !== 'win32') return;
  const exe = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.exe`);
  const icon = path.join(context.packager.projectDir, 'assets', 'icon.ico');
  const rcedit = path.join(context.packager.projectDir, 'scripts', 'rcedit-x64.exe');
  execFileSync(rcedit, [exe, '--set-icon', icon], { stdio: 'inherit' });
};
