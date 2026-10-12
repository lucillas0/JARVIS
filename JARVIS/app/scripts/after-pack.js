/* afterPack: incrusta el icono de JARVIS en el .exe.

   signAndEditExecutable esta en false porque winCodeSign no se puede extraer en
   este entorno, asi que el icono lo ponemos nosotros con rcedit.

   Las dos rutas importan: los assets viven un nivel por encima de la app
   (../assets), y rcedit va en scripts/. Ademas, si rcedit no esta (por ejemplo
   en un clon sin binarios) no se tumba la compilacion: se avisa y se sigue,
   porque un instalador sin icono es mejor que un instalador que no sale. */
const { execFileSync } = require('child_process');
const fs = require('fs');
const path = require('path');

exports.default = async function (context) {
  if (context.electronPlatformName !== 'win32') return;

  const exe = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.exe`);
  const icon = path.join(context.packager.projectDir, '..', 'assets', 'icon.ico');
  const rcedit = path.join(context.packager.projectDir, 'scripts', 'rcedit-x64.exe');

  if (!fs.existsSync(rcedit)) {
    console.warn('[after-pack] No encuentro rcedit en ' + rcedit + '; el .exe sale sin icono.');
    return;
  }
  if (!fs.existsSync(icon)) {
    console.warn('[after-pack] No encuentro el icono en ' + icon + '; el .exe sale sin icono.');
    return;
  }
  try {
    execFileSync(rcedit, [exe, '--set-icon', icon], { stdio: 'inherit' });
  } catch (err) {
    console.warn('[after-pack] rcedit no pudo poner el icono (' + err.message + '); se sigue.');
  }
};