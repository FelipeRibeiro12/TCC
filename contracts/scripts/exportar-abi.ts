import { artifacts, config } from "hardhat";
import * as fs from "fs";
import * as path from "path";

const CONTRACT_NAMES = ["AssetTwin", "ShipmentEscrow"];

async function main() {
  // parte de paths.root e não do cwd, para funcionar de qualquer pasta
  const abiDir = path.resolve(config.paths.root, "..", "abi");
  fs.mkdirSync(abiDir, { recursive: true });

  for (const name of CONTRACT_NAMES) {
    const artifact = await artifacts.readArtifact(name);
    const file = path.join(abiDir, `${name}.json`);

    // só a ABI, com quebra de linha no final para o diff do git ficar limpo
    fs.writeFileSync(file, JSON.stringify(artifact.abi, null, 2) + "\n");
    console.log(`${name}: ${artifact.abi.length} entradas em ${file}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
