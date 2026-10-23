import { artifacts, config } from "hardhat";
import * as fs from "fs";
import * as path from "path";

const CONTRACT_NAMES = ["AssetTwin", "ShipmentEscrow"];

// parte de paths.root e não do cwd, para funcionar de qualquer pasta
export const abiDir = path.resolve(config.paths.root, "..", "abi");

export async function exportAbi() {
  fs.mkdirSync(abiDir, { recursive: true });

  for (const name of CONTRACT_NAMES) {
    const artifact = await artifacts.readArtifact(name);
    const file = path.join(abiDir, `${name}.json`);

    // só a ABI, com quebra de linha no final para o diff do git ficar limpo
    fs.writeFileSync(file, JSON.stringify(artifact.abi, null, 2) + "\n");
    console.log(`${name}: ${artifact.abi.length} entradas em ${file}`);
  }
}

// o deploy importa este arquivo, e sem essa checagem a exportação rodaria duas vezes
if (require.main === module) {
  exportAbi().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
